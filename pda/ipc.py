# -*- coding: utf-8 -*-
"""单实例 + IPC。

单实例：两层锁，同一数据目录同一时间只有一个进程写 SQLite 和 chromadb（chroma 不支持多进程写）：
  1. 命名互斥体 Local\\PDA_KnowledgeAssistant_<数据目录哈希>（run.py 在 import 重依赖之前抢占）。
     名字带数据目录哈希：PDA_DATA_DIR 不同的两个副本互不阻塞，右键请求也不会转发错实例。
  2. 数据目录下 .instance.lock 的字节锁（msvcrt.locking）。Local\\ 互斥体只在当前登录会话内生效，
     快速切换用户 / RDP 同时运行同一个绿色版目录时，靠文件锁保证只有一个写者。
只有主程序会拿这两把锁并写库；右键 --add 进程只是客户端：主程序没开时最小化拉起它，再转发路径。

IPC：持有锁的进程监听 127.0.0.1 上系统分配的端口，端口与随机 token 写入 data/ipc_token
（JSON，仅当前用户可读）。一行 JSON 协议：
  {"token": "...", "paths": ["D:/..."]}     收录文件（右键菜单 --add 转发）
  {"token": "...", "action": "activate"}    激活主窗口（无参二次启动）
  {"token": "...", "action": "quit"}        正常退出（卸载程序先这样请求，避免强杀时库写到一半）
响应：
  "ok"               GUI 线程已接受（已进入收录队列 / 已激活）——只有真正接受了才回 ok，
                     进程退出前的最后几秒收到的请求不会"回了 ok 却丢掉"
  "busy"             对方不处理这类消息或正在退出（客户端重试，旧实例退出后会拉起新实例）
  "invalid:<原因>"   路径不合法（相对路径 / 不存在 / 网络路径 / 盘符根目录）

安全：token 不匹配的连接直接丢弃；每个连接总时长 2 秒、消息 64KB 上限，慢速连接占不住服务；
IPC 收到的路径拒绝 UNC 网络路径（防止被诱导访问 \\\\攻击者\\共享 泄露 NTLM 哈希）和盘符根目录。
"""
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time

from . import config

HOST = "127.0.0.1"

_CLIENT_TIMEOUT = 6.0      # 客户端等回复：要比服务端等 GUI 线程接受的时间长
_GUI_ACCEPT_TIMEOUT = 4.0  # 服务端等 GUI 线程处理消息的上限（GUI 卡住/正在退出则回 busy）
_CONN_DEADLINE = 2.0       # 单个连接读完整条消息的总时长上限
_MAX_MSG = 64 * 1024
_MAX_HANDLERS = 8

_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_SYNCHRONIZE = 0x00100000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.OpenMutexW.restype = ctypes.c_void_p
_kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_kernel32.WaitForSingleObject.restype = ctypes.c_uint32
_kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

_instance_mutex = None  # 进程存活期间一直持有
_instance_lock_file = None


def _data_dir_key() -> str:
    path = os.path.normcase(os.path.abspath(str(config.DATA_DIR)))
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


def _mutex_name() -> str:
    return f"Local\\PDA_KnowledgeAssistant_{_data_dir_key()}"


def _token_path():
    return config.DATA_DIR / "ipc_token"


def _lock_path():
    return config.DATA_DIR / ".instance.lock"


def _current_session_id() -> int:
    sid = ctypes.c_uint32(0)
    try:
        if _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return int(sid.value)
    except Exception:
        pass
    return -1


# ---------- 单实例锁 ----------

def _try_file_lock():
    """数据目录字节锁（跨登录会话有效）。成功返回打开的文件对象；被占返回 None；
    数据目录不可写等无法建锁的情况返回 True（不阻止启动，退化为只靠互斥体）。"""
    import msvcrt

    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        f = open(_lock_path(), "a+b")
    except OSError:
        return True
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


def acquire_instance_lock(wait_sec: float = 0.0) -> bool:
    """尝试成为唯一实例。wait_sec > 0 时最多等待对方释放。成功返回 True。"""
    global _instance_mutex, _instance_lock_file
    if _instance_mutex is not None:
        return True
    start = time.monotonic()
    deadline = start + max(wait_sec, 0)
    handle = _kernel32.CreateMutexW(None, False, _mutex_name())
    if not handle:
        return True  # 创建失败（极少见）：不阻止启动
    while True:
        ms = int(max(deadline - time.monotonic(), 0) * 1000)
        rc = _kernel32.WaitForSingleObject(handle, ms)
        if rc in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            lock = _try_file_lock()
            if lock is not None:
                _instance_mutex = handle
                _instance_lock_file = lock if lock is not True else None
                _remove_stale_token()
                return True
            # 互斥体拿到了，但文件锁被占：另一个会话里的实例，或客户端 instance_running()
            # 正在瞬时探测（所以至少重试约 1 秒）
            _kernel32.ReleaseMutex(handle)
            if time.monotonic() >= max(deadline, start + 1.0):
                break
            time.sleep(0.3)
            continue
        break
    _kernel32.CloseHandle(handle)
    return False


def instance_running() -> bool:
    """当前会话内是否有实例持有互斥体（用来避免把 token 发给占用了旧端口的陌生进程）。"""
    handle = _kernel32.OpenMutexW(_SYNCHRONIZE, False, _mutex_name())
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    # 另一个会话里的实例：互斥体不可见，只能看文件锁
    lock = _try_file_lock()
    if lock is None:
        return True
    if lock is not True:
        lock.close()
    return False


def spawn_guard():
    """右键多选会同时起多个 --add 进程：只让拿到本互斥体的那个去拉起主程序。
    拿到返回句柄（用完交给 release_spawn_guard），没拿到返回 None（只等待转发）。"""
    handle = _kernel32.CreateMutexW(None, False, f"Local\\PDA_Spawn_{_data_dir_key()}")
    if not handle:
        return None
    if _kernel32.WaitForSingleObject(handle, 0) in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return handle
    _kernel32.CloseHandle(handle)
    return None


def release_spawn_guard(handle):
    if handle:
        _kernel32.ReleaseMutex(handle)
        _kernel32.CloseHandle(handle)


def _remove_stale_token():
    """刚成为实例时删掉上次崩溃残留的 token 文件（里面的端口可能已被别的进程占用）。"""
    try:
        _token_path().unlink()
    except OSError:
        pass


# ---------- 客户端 ----------

def _read_endpoint():
    try:
        data = json.loads(_token_path().read_text(encoding="utf-8"))
        return int(data["port"]), str(data["token"]), int(data.get("session", -1))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def running_in_other_session() -> bool:
    """运行中的实例是否在另一个 Windows 登录会话里（同一数据目录，窗口在这边看不到）。"""
    ep = _read_endpoint()
    if ep is None:
        return False
    own = _current_session_id()
    return ep[2] >= 0 and own >= 0 and ep[2] != own


def send(msg: dict, timeout: float = _CLIENT_TIMEOUT):
    """向运行中的实例发送消息。返回 "ok" / "busy" / "invalid:<原因>"；没在跑/连不上返回 None。"""
    if not instance_running():
        return None
    ep = _read_endpoint()
    if ep is None:
        return None
    port, token, _ = ep
    try:
        with socket.create_connection((HOST, port), timeout=min(timeout, 2.0)) as sock:
            payload = dict(msg, token=token)
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            sock.settimeout(timeout)
            reply = b""
            while len(reply) < 4096:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
            text = reply.decode("utf-8", errors="replace").strip()
            return text or None
    except OSError:
        return None


def send_message(msg: dict, timeout: float = _CLIENT_TIMEOUT) -> bool:
    """兼容旧接口：对方回 "ok" 才算成功。"""
    return send(msg, timeout) == "ok"


def send_message_retry(msg: dict, total_sec: float) -> bool:
    """对方刚启动、IPC 还没就绪（重依赖加载约 5-6 秒）时持续重试。"""
    deadline = time.monotonic() + total_sec
    while True:
        if send_message(msg):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


# ---------- 路径校验 ----------

def validate_paths(paths) -> tuple:
    """IPC 收到的路径校验，返回 (合法路径列表, 第一条拒绝原因或 "")。"""
    ok, reason = [], ""
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            continue
        p = p.strip()
        norm = p.replace("/", "\\")
        if norm.startswith("\\\\?\\") and not norm.upper().startswith("\\\\?\\UNC\\"):
            norm = norm[4:]  # \\?\C:\... 长路径前缀：去掉后按本地路径判断
        if norm.startswith("\\\\"):
            reason = reason or f"不接受网络路径（请拖入主窗口收录）：{p}"
            continue
        if not os.path.isabs(norm) or len(norm) < 3 or norm[1] != ":":
            reason = reason or f"不是绝对路径：{p}"
            continue
        if os.path.splitdrive(norm)[1].strip("\\") == "":
            reason = reason or f"不接受整个磁盘：{p}"
            continue
        if not os.path.exists(norm):
            reason = reason or f"路径不存在：{p}"
            continue
        ok.append(p)
    return ok, reason


# ---------- token 文件（仅当前用户可读） ----------

def _restrict_to_current_user(path) -> bool:
    """去掉继承的 ACL，只给当前用户完全控制。绿色版放在 D:\\ 这类目录时，
    继承的 ACL 通常允许 Authenticated Users 读取，其他登录用户就能拿到 token。"""
    try:
        import ntsecuritycon
        import win32api
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid)
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            sd,
        )
        return True
    except Exception:
        pass
    try:
        user = os.environ.get("USERNAME")
        if not user:
            return False
        rc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10,
        ).returncode
        return rc == 0
    except Exception:
        return False


def _write_token_file(port: int, token: str):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _token_path().with_suffix(".tmp")
    try:
        tmp.unlink()
    except OSError:
        pass
    # 先建空文件、收紧 ACL，再写内容；同卷 rename 保留源文件的安全描述符
    tmp.write_bytes(b"")
    if not _restrict_to_current_user(tmp):
        # ACL 收不紧（pywin32 与 icacls 都失败）：token 可能被其他登录用户读到，不启用 IPC
        try:
            tmp.unlink()
        except OSError:
            pass
        raise OSError("无法把 ipc_token 设为仅当前用户可读")
    payload = {"port": port, "token": token, "pid": os.getpid(), "session": _current_session_id()}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, _token_path())


def __getattr__(name):
    # 服务端依赖 Qt，放在 ipc_server：右键 --add / --quit 客户端进程只用本模块，不加载 Qt
    if name == "IpcServer":
        from .ipc_server import IpcServer

        return IpcServer
    raise AttributeError(name)
