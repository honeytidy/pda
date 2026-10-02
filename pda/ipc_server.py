# -*- coding: utf-8 -*-
"""IPC 服务端（协议与安全说明见 ipc.py）。"""
import hmac
import json
import secrets
import socket
import threading
import time

from PySide6.QtCore import QThread, Signal

from .ipc import (_CONN_DEADLINE, _GUI_ACCEPT_TIMEOUT, _MAX_HANDLERS, _MAX_MSG, HOST,
                  _token_path, _write_token_file, validate_paths)


class IpcServer(QThread):
    """监听线程 + 每连接一个短命处理线程。

    处理线程校验通过后把消息交给 GUI 线程（_dispatch 经排队信号在 GUI 线程执行），
    GUI 线程发射 message_received 并确认后才回 "ok"；GUI 线程在规定时间内没处理
    （卡住 / 事件循环已退出）或服务已 stop，则回 "busy"，客户端据此重试。

    accept：本进程处理的消息类型（"paths" / "activate" / "quit"），其他类型回 "busy"。
    """

    message_received = Signal(dict)
    failed = Signal(str)          # 监听/写 token 失败：IPC 不可用（右键转发、二次激活会失效）
    _incoming = Signal(int)       # 内部：处理线程 → GUI 线程

    def __init__(self, parent=None, accept=("paths", "activate")):
        super().__init__(parent)
        self._accept = set(accept)
        self._lock = threading.Lock()
        self._running = True       # stop() 可能早于 run() 执行到监听，用它判断
        self._closing = False
        self._server = None
        self._token = secrets.token_hex(16)
        self._token_written = False
        self._pending = {}         # id -> [event, msg, result]
        self._next_id = 0
        self._handlers = threading.BoundedSemaphore(_MAX_HANDLERS)
        self._incoming.connect(self._dispatch)  # 接收者在 GUI 线程 → 排队执行

    # ----- 监听线程 -----

    def run(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            server.bind((HOST, 0))  # 系统分配端口：不会被别的程序占住固定端口
            server.listen(16)
            server.settimeout(0.5)  # 让 accept 循环能响应停止
            port = server.getsockname()[1]
        except OSError as e:
            self.failed.emit(f"后台通信启动失败（{e}），右键菜单转发与二次启动激活不可用")
            return
        with self._lock:
            if not self._running:
                server.close()
                return
            try:
                _write_token_file(port, self._token)
                self._token_written = True
            except OSError as e:
                server.close()
                self.failed.emit(f"后台通信启动失败（写入 ipc_token 失败：{e}）")
                return
            self._server = server
        while self._running:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._handlers.acquire(blocking=False):
                conn.close()  # 并发连接过多：直接丢弃，客户端会重试
                continue
            threading.Thread(target=self._handle_safe, args=(conn,), daemon=True).start()
        try:
            server.close()
        except OSError:
            pass

    # ----- 连接处理线程 -----

    def _handle_safe(self, conn):
        try:
            self._handle(conn)
        except Exception:
            pass
        finally:
            self._handlers.release()

    def _handle(self, conn: socket.socket):
        with conn:
            deadline = time.monotonic() + _CONN_DEADLINE
            data = b""
            while not data.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(data) > _MAX_MSG:
                    return  # 慢速/超大连接：不回应直接断开
                conn.settimeout(remaining)
                chunk = conn.recv(8192)
                if not chunk:
                    break
                data += chunk
            msg = json.loads(data.decode("utf-8").strip())
            if not isinstance(msg, dict):
                return
            if not hmac.compare_digest(str(msg.pop("token", "")).encode(), self._token.encode()):
                return  # 鉴权失败：不回应
            if msg.get("action") in ("activate", "quit"):
                kind = msg["action"]
                msg = {"action": kind}
            elif isinstance(msg.get("paths"), list) and msg["paths"]:
                kind = "paths"
            else:
                kind = None
            if kind is None or kind not in self._accept or self._closing:
                conn.sendall(b"busy")
                return
            if kind == "paths":
                paths, reason = validate_paths(msg["paths"])
                if not paths:
                    conn.sendall(f"invalid:{reason or '没有可收录的路径'}".encode("utf-8"))
                    return
                msg = {"paths": paths}
            conn.sendall(self._hand_to_gui(msg).encode("utf-8"))

    def _hand_to_gui(self, msg: dict) -> str:
        event = threading.Event()
        with self._lock:
            if self._closing:
                return "busy"
            self._next_id += 1
            mid = self._next_id
            entry = [event, msg, None]
            self._pending[mid] = entry
        self._incoming.emit(mid)
        event.wait(_GUI_ACCEPT_TIMEOUT)
        with self._lock:
            self._pending.pop(mid, None)
            if entry[2] is None:
                entry[2] = "busy"  # 超时：标记为已拒绝，之后 GUI 线程再轮到它也不会处理
            return entry[2]

    # ----- GUI 线程 -----

    def _dispatch(self, mid: int):
        with self._lock:
            entry = self._pending.get(mid)
            if entry is None or entry[2] is not None:
                return
            if self._closing:
                entry[2] = "busy"
                entry[0].set()
                return
            entry[2] = "ok"
        try:
            self.message_received.emit(entry[1])
        finally:
            entry[0].set()

    def stop(self):
        """停止接受新请求：已在等 GUI 线程的请求立即回 busy（客户端重试），不阻塞在处理线程上。"""
        with self._lock:
            self._running = False
            self._closing = True
            for entry in self._pending.values():
                if entry[2] is None:
                    entry[2] = "busy"
                entry[0].set()
            server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        self.wait(2000)
        if self._token_written:
            try:
                data = json.loads(_token_path().read_text(encoding="utf-8"))
                if data.get("token") == self._token:
                    _token_path().unlink()
            except (OSError, ValueError):
                pass
