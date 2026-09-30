# -*- coding: utf-8 -*-
"""全局热键：ctypes 直调 RegisterHotKey + 专用线程跑 GetMessage 消息泵。

- Ctrl+Shift+Q：剪贴板存笔记（triggered）
- Ctrl+Shift+A：收录资源管理器选中项（selection_ingest_requested）

信号经 QueuedConnection 到 GUI 线程处理。注册失败（被占用等）发 failed 信号，
不影响软件运行。
"""
import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal

user32 = ctypes.WinDLL("user32", use_last_error=True)  # use_last_error 才能拿到真实错误码

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_Q = 0x51
VK_A = 0x41
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# (id, vk, 名称)
_HOTKEYS = [
    (1, VK_Q, "Ctrl+Shift+Q"),
    (2, VK_A, "Ctrl+Shift+A"),
]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class HotkeyThread(QThread):
    """RegisterHotKey 只在注册它的线程里有效，故注册与消息循环都放在 run()。"""

    triggered = Signal()                       # Ctrl+Shift+Q
    selection_ingest_requested = Signal()      # Ctrl+Shift+A
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native_tid = None
        self._ready = threading.Event()   # 消息队列已建立，PostThreadMessage 可以投递
        self._stop_requested = False

    def run(self):
        self._native_tid = threading.get_native_id()  # Win32 线程 id，stop() 要用
        # PeekMessage 强制建立线程消息队列，之后 stop() 发的 WM_QUIT 才不会丢
        msg = _MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready.set()
        if self._stop_requested:
            return
        registered = []
        for hotkey_id, vk, name in _HOTKEYS:
            if user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, vk):
                registered.append(hotkey_id)
            else:
                err = ctypes.get_last_error()
                self.failed.emit(f"热键 {name} 注册失败（错误码 {err}，可能被占用）")
        if not registered:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message != WM_HOTKEY:
                    continue
                if msg.wParam == 1:
                    self.triggered.emit()
                elif msg.wParam == 2:
                    self.selection_ingest_requested.emit()
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    def stop(self):
        """向消息线程发 WM_QUIT 让 GetMessage 退出（run 结束会自动 UnregisterHotKey）。"""
        self._stop_requested = True
        if not self.isRunning():
            return
        self._ready.wait(2.0)
        # 投递失败（队列未就绪等）时重试，直到线程退出
        for _ in range(25):
            if self._native_tid:
                user32.PostThreadMessageW(self._native_tid, WM_QUIT, 0, 0)
            if self.wait(200):
                return
        # 不用 terminate()：强杀持有 GIL 的 Python 线程会让解释器死锁。
        # 脱离父对象并保持引用，避免随主窗口析构触发 "QThread destroyed while running"；
        # 进程退出时系统会回收线程与热键注册
        self.setParent(None)
        _orphans.append(self)


_orphans = []
