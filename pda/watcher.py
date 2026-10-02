# -*- coding: utf-8 -*-
"""监控文件夹自动收录。

优先用 watchdog 监听文件创建/修改事件；watchdog 不可用时降级为
QTimer 每 10 秒快照对比。统一做 ~2 秒防抖，防抖到期后还要确认文件
"写入稳定"（大小/修改时间连续两次检查不变、可以打开）才收录，否则继续等。
发现的文件通过 files_ready 信号交给 UI 层走正常入库管道（后台线程）。
重复/变更判断由 ingest 按"来源路径+修改时间+大小"处理，watcher 不管。

Office 临时锁文件（~$x.docx）、下载中的临时文件、隐藏/系统文件不收。
"""
import os
import stat
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import config, parsers

DEBOUNCE_SEC = 2.0
POLL_INTERVAL_MS = 10_000
# 启动时不存在的监控目录（移动硬盘 / 网络盘未挂载）每隔这么久重试一次
MISSING_RETRY_MS = 60_000
# 文件一直不稳定（持续写入 / 被独占）时最多等这么久，之后放弃本次事件
STABLE_GIVE_UP_SEC = 300.0

_TEMP_PREFIXES = ("~$", ".~", "~")
_TEMP_SUFFIXES = (".tmp", ".crdownload", ".part", ".partial", ".download", ".temp")
_SKIP_ATTRS = (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
               | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
               | getattr(stat, "FILE_ATTRIBUTE_TEMPORARY", 0x100))


def is_candidate(path: str) -> bool:
    """监控模式下是否考虑收录：支持的格式，且不是临时/锁定/隐藏文件。"""
    name = os.path.basename(path)
    lower = name.lower()
    if not name or name.startswith(".") or lower.startswith(_TEMP_PREFIXES):
        return False
    if lower.endswith(_TEMP_SUFFIXES):
        return False
    if Path(name).suffix.lower() not in parsers.SUPPORTED_EXTS:
        return False
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return True  # 可能已被删除 / 暂时拿不到属性：交给后续 isfile 判断
    return not (attrs & _SKIP_ATTRS)


def _signature(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _can_open(path: str) -> bool:
    """文件还被写入方独占（WinError 32）时打不开，等下一轮再试。"""
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _collect_supported(root: str) -> dict:
    """递归收集目录下支持的文件：{path: (mtime, size)}"""
    result = {}
    for child in Path(root).rglob("*"):
        try:
            if child.is_file() and is_candidate(str(child)):
                st = child.stat()
                result[str(child)] = (st.st_mtime, st.st_size)
        except OSError:
            continue
    return result


class FolderWatcher(QObject):
    """监控文件夹，发现新文件/变更文件后发射 files_ready(list[str])。"""

    files_ready = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folders = []
        self._missing = []  # 配置了但当前不存在的目录，定时重试
        # path -> [最后事件时间, 首次事件时间, 上次检查到的签名]
        self._pending = {}
        # watchdog 回调 / 轮询线程写 _pending、_snapshot，GUI 线程读/删，必须加锁
        self._lock = threading.Lock()
        self._generation = 0  # 每次 stop 自增：旧轮询线程据此丢弃过期结果
        self._poll_thread = None
        self._observer = None
        self._poll_timer = None
        self._snapshot = None  # 降级模式的目录快照
        # 防抖检查定时器（GUI 线程）
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._flush_pending)
        self._missing_timer = QTimer(self)
        self._missing_timer.setInterval(MISSING_RETRY_MS)
        self._missing_timer.timeout.connect(self._retry_missing)

    # ---------- 生命周期 ----------

    def start(self, folders: list):
        self.stop()
        folders = [f for f in folders if isinstance(f, str) and f.strip()]
        self._folders = [f for f in folders if os.path.isdir(f)]
        self._missing = [f for f in folders if not os.path.isdir(f)]
        if self._missing:
            self._missing_timer.start()
        if not self._folders:
            return
        if not self._start_watchdog():
            self._start_polling()
        else:
            # watchdog 只报告启动之后的变化：程序没开期间放进来的文件补扫一次
            # （已收录且未变化的由 ingest 按签名跳过，代价很小）
            self._start_initial_scan()
        self._debounce_timer.start()

    def stop(self):
        self._debounce_timer.stop()
        self._missing_timer.stop()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        with self._lock:
            self._generation += 1
            self._pending.clear()
            self._snapshot = None
        # 旧轮询线程不必等：它完成时发现代数变了会直接丢弃结果
        self._poll_thread = None

    def restart(self):
        self.start(config.get_watch_folders())

    def backend_name(self) -> str:
        if self._observer is not None:
            return "watchdog"
        if self._poll_timer is not None:
            return "定时扫描"
        return "未运行"

    def _retry_missing(self):
        # 移动硬盘 / 网络盘挂载后自动开始监控
        if any(os.path.isdir(f) for f in self._missing):
            self.start(self._folders + self._missing)

    # ---------- watchdog 模式 ----------

    def _start_watchdog(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:
            return False

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    watcher._on_fs_event(event.src_path)

            def on_moved(self, event):
                # 浏览器下载完成（.crdownload → x.pdf）、Office/编辑器"写临时文件再改名"式保存
                # 都只产生 moved 事件，目标路径才是真正的文件
                if not event.is_directory:
                    watcher._on_fs_event(event.dest_path)

        try:
            observer = Observer()
            handler = Handler()
            for folder in self._folders:
                observer.schedule(handler, folder, recursive=True)
            observer.start()
        except Exception:
            return False
        self._observer = observer
        return True

    def _on_fs_event(self, path: str):
        # watchdog 回调在 observer 线程：只记录时间戳，由 GUI 线程的防抖定时器处理
        if is_candidate(path):
            with self._lock:
                self._touch_locked(path, time.time())

    def _touch_locked(self, path: str, now: float):
        entry = self._pending.get(path)
        if entry is None:
            self._pending[path] = [now, now, None]
        else:
            entry[0] = now

    def _start_initial_scan(self):
        with self._lock:
            generation = self._generation
        threading.Thread(
            target=self._initial_scan_worker, args=(list(self._folders), generation), daemon=True
        ).start()

    def _initial_scan_worker(self, folders: list, generation: int):
        current = {}
        for folder in folders:
            current.update(_collect_supported(folder))
        now = time.time()
        with self._lock:
            if generation != self._generation:
                return
            for path in current:
                self._touch_locked(path, now)

    # ---------- 定时扫描降级模式 ----------

    def _start_polling(self):
        with self._lock:
            self._snapshot = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_once)
        self._poll_timer.start()
        self._poll_once()

    def _poll_once(self):
        # 全量 rglob 可能很慢（大目录/网络盘），放到后台线程，不卡 GUI
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        with self._lock:
            generation = self._generation
        self._poll_thread = threading.Thread(
            target=self._poll_worker, args=(list(self._folders), generation), daemon=True
        )
        self._poll_thread.start()

    def _poll_worker(self, folders: list, generation: int):
        current = {}
        for folder in folders:
            current.update(_collect_supported(folder))
        now = time.time()
        with self._lock:
            if generation != self._generation:
                return  # 期间 stop/start 过：结果属于旧的目录集合，丢弃
            # 首轮（快照为空）也全部交给 ingest：程序没开期间新增/修改的文件靠它补收
            previous = self._snapshot or {}
            for path, sig in current.items():
                if previous.get(path) != sig:
                    self._touch_locked(path, now)
            self._snapshot = current

    # ---------- 防抖输出 ----------

    def _flush_pending(self):
        now = time.time()
        with self._lock:
            due = [(p, e) for p, e in self._pending.items() if now - e[0] >= DEBOUNCE_SEC]
        ready, drop = [], []
        for path, entry in due:
            if not os.path.isfile(path):
                drop.append(path)
                continue
            sig = _signature(path)
            # 写入稳定：连续两次检查签名一致且能打开，否则继续等（网络盘慢速拷贝、
            # 程序间隔 >2 秒才写一次、文件仍被独占等）
            if sig is not None and sig == entry[2] and _can_open(path):
                ready.append(path)
            elif now - entry[1] > STABLE_GIVE_UP_SEC:
                drop.append(path)
            else:
                with self._lock:
                    if self._pending.get(path) is entry:
                        entry[2] = sig
                        entry[0] = now - DEBOUNCE_SEC + 1.0  # 约 1 秒后再检查
        with self._lock:
            for path in ready + drop:
                self._pending.pop(path, None)
        if ready:
            self.files_ready.emit(ready)
