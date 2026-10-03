# -*- coding: utf-8 -*-
"""全局热键：ctypes 直调 RegisterHotKey + 专用线程跑 GetMessage 消息泵。

按键可在界面里自定义（config.HOTKEY_ACTIONS / get_hotkeys），默认：
- Ctrl+Shift+Q：剪贴板存笔记（triggered）
- Ctrl+Shift+A：收录资源管理器选中项（selection_ingest_requested）
- Ctrl+Alt+Space：呼出/隐藏主界面（show_requested）

改键时主窗口停掉旧线程、按新配置起一个新线程（RegisterHotKey 只在注册它的线程里有效）。
信号经 QueuedConnection 到 GUI 线程处理。注册失败（被占用等）发 failed 信号，
不影响软件运行。
"""
import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence

user32 = ctypes.WinDLL("user32", use_last_error=True)  # use_last_error 才能拿到真实错误码

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# 动作 -> 热键 id（WM_HOTKEY 的 wParam）
_ACTION_IDS = {"clipboard": 1, "selection": 2, "show": 3}
_PROBE_ID = 0xBF00  # is_available() 试注册用，不与正式 id 冲突

# Qt 键 -> Win32 虚拟键码（字母/数字的 Qt 值就是 ASCII，与 VK 相同，单独处理）
_SPECIAL_VK = {
    Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23,
    Qt.Key_Home: 0x24, Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27,
    Qt.Key_Down: 0x28, Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
    Qt.Key_Semicolon: 0xBA, Qt.Key_Equal: 0xBB, Qt.Key_Comma: 0xBC, Qt.Key_Minus: 0xBD,
    Qt.Key_Period: 0xBE, Qt.Key_Slash: 0xBF, Qt.Key_QuoteLeft: 0xC0,
    Qt.Key_BracketLeft: 0xDB, Qt.Key_Backslash: 0xDC, Qt.Key_BracketRight: 0xDD,
    Qt.Key_Apostrophe: 0xDE,
}
_SPECIAL_VK = {int(getattr(k, "value", k)): v for k, v in _SPECIAL_VK.items()}
_KEY_F1 = int(getattr(Qt.Key_F1, "value", Qt.Key_F1))
_RESERVED_CTRL = {ord(c) for c in "ACVXZYSFPNOW"}


def _int(v) -> int:
    return int(getattr(v, "value", v))


def parse(seq: str):
    """把 "Ctrl+Shift+Q" 这样的按键转成 (Win32 修饰键, 虚拟键码)。

    返回 (mods, vk, 错误信息)：成功时错误信息为 ""；不合法时 mods/vk 为 0。
    """
    ks = QKeySequence.fromString(seq or "", QKeySequence.PortableText)
    if ks.isEmpty():
        return 0, 0, "未设置"
    if ks.count() > 1:
        return 0, 0, "只能是一组按键"
    combo = ks[0]
    key = _int(combo.key())
    qmods = _int(combo.keyboardModifiers())
    mods = 0
    if qmods & _int(Qt.ControlModifier):
        mods |= MOD_CONTROL
    if qmods & _int(Qt.AltModifier):
        mods |= MOD_ALT
    if qmods & _int(Qt.ShiftModifier):
        mods |= MOD_SHIFT
    if qmods & _int(Qt.MetaModifier):
        mods |= MOD_WIN
    # 全局热键会吞掉所有程序里的这组按键：只按 Shift 或不按修饰键会让正常打字失灵
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return 0, 0, "需要包含 Ctrl、Alt 或 Win 键"
    # Ctrl+C/V/X 这类是所有程序都在用的编辑快捷键，注册成全局热键会让复制粘贴失灵
    if mods == MOD_CONTROL and key in _RESERVED_CTRL:
        return 0, 0, "这是常用的系统快捷键，请加上 Shift 或 Alt"
    if ord("A") <= key <= ord("Z") or ord("0") <= key <= ord("9"):
        vk = key
    elif _KEY_F1 <= key < _KEY_F1 + 24:
        vk = 0x70 + (key - _KEY_F1)
    elif key in _SPECIAL_VK:
        vk = _SPECIAL_VK[key]
    else:
        return 0, 0, "不支持这个按键，请换一个字母、数字或 F 键"
    return mods, vk, ""


def display(seq: str) -> str:
    """界面显示用的按键文字（"Ctrl+Shift+Q"），未设置返回 ""。"""
    ks = QKeySequence.fromString(seq or "", QKeySequence.PortableText)
    if ks.isEmpty():
        return ""
    # Qt 把 Windows 徽标键叫 Meta，Windows 用户认的是 Win
    return ks.toString(QKeySequence.NativeText).replace("Meta+", "Win+")


def is_available(seq: str) -> bool:
    """试注册一下看这组按键有没有被别的程序占用（立即注销）。

    注意本程序自己正在用的按键也会被判为"占用"，调用方要先排除。
    """
    mods, vk, err = parse(seq)
    if err:
        return False
    if not user32.RegisterHotKey(None, _PROBE_ID, mods | MOD_NOREPEAT, vk):
        return False
    user32.UnregisterHotKey(None, _PROBE_ID)
    return True


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
    """RegisterHotKey 只在注册它的线程里有效，故注册与消息循环都放在 run()。

    bindings：{"clipboard": "Ctrl+Shift+Q", "selection": "Ctrl+Shift+A", "show": "Ctrl+Alt+Space"}，值为空表示不启用。
    """

    triggered = Signal()                       # 剪贴板存笔记
    selection_ingest_requested = Signal()      # 收录资源管理器选中项
    show_requested = Signal()                  # 呼出/隐藏主界面
    failed = Signal(str)

    def __init__(self, bindings: dict, parent=None):
        super().__init__(parent)
        self._bindings = dict(bindings)
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
        for action, hotkey_id in _ACTION_IDS.items():
            seq = self._bindings.get(action) or ""
            if not seq:
                continue  # 用户关掉了这个热键
            mods, vk, err = parse(seq)
            name = display(seq) or seq
            if err:
                self.failed.emit(f"快捷键 {name} 无效：{err}")
                continue
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                registered.append(hotkey_id)
            else:
                code = ctypes.get_last_error()
                reason = ("已被其他程序占用" if code == ERROR_HOTKEY_ALREADY_REGISTERED
                          else f"错误码 {code}")
                self.failed.emit(f"快捷键 {name} 注册失败（{reason}），可在「快捷键」里换一个")
        if not registered:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message != WM_HOTKEY:
                    continue
                if msg.wParam == _ACTION_IDS["clipboard"]:
                    self.triggered.emit()
                elif msg.wParam == _ACTION_IDS["selection"]:
                    self.selection_ingest_requested.emit()
                elif msg.wParam == _ACTION_IDS["show"]:
                    self.show_requested.emit()
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
