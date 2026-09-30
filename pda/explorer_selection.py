# -*- coding: utf-8 -*-
"""读取 Windows 资源管理器前台窗口中选中的文件/文件夹路径。

用 Shell.Application COM 遍历资源管理器窗口，找到与前台窗口 HWND 相同且
ClassName 为 CabinetWClass 的窗口，取其 Document.SelectedItems()。
前台不是资源管理器（或选中的是虚拟项）时返回空列表。

COM 调用约定：函数内部用 ole32.CoInitializeEx 自管理套间（仅当本次调用
新建套间时才配对 CoUninitialize，不干扰调用方已有的 COM 状态）。
"""
import ctypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

EXPLORER_CLASS = "CabinetWClass"
DESKTOP_CLASSES = ("Progman", "WorkerW")  # 桌面：前台是桌面时取桌面上的选中项
COINIT_APARTMENTTHREADED = 0x2
SWC_DESKTOP = 0x8
SWFO_NEEDDISPATCH = 0x1


def foreground_target():
    """前台窗口是资源管理器 / 桌面时返回 (hwnd, 是否桌面)，否则 None。

    必须在热键触发的当下（GUI 线程）调用：之后前台窗口可能已经变了。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(hwnd, buf, 256) == 0:
        return None
    if buf.value == EXPLORER_CLASS:
        return hwnd, False
    if buf.value in DESKTOP_CLASSES:
        return hwnd, True
    return None


def get_explorer_selected_paths(target=None) -> list:
    """返回前台资源管理器窗口（或桌面）选中的路径列表；前台不是资源管理器返回 []。

    target 为 foreground_target() 的结果；不传则现取。COM 跨进程调用可能被卡住的
    资源管理器阻塞，调用方应放在工作线程里执行。
    """
    if target is None:
        target = foreground_target()
    if target is None:
        return []
    hwnd, is_desktop = target
    try:
        import win32com.client
    except ImportError as e:
        raise SelectionError(f"pywin32 组件不可用（{e}）") from e

    # COM 初始化：S_OK(0)=本次新初始化才负责配对释放；S_FALSE(1)=线程已初始化，
    # 不能 CoUninitialize，否则会把调用方的 COM 套间计数弄崩
    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    should_uninit = hr == 0
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        if is_desktop:
            window = shell.Windows().FindWindowSW(0, 0, SWC_DESKTOP, 0, SWFO_NEEDDISPATCH)
        else:
            # Win11 多标签：同一窗口的所有标签 HWND 相同，要挑出当前可见（活动）的那个标签
            candidates = []
            for window in shell.Windows():
                try:
                    if window.HWND == hwnd:
                        candidates.append(window)
                except Exception:
                    continue
            window = _active_tab(candidates)
        if window is None:
            return []
        paths = []
        for item in window.Document.SelectedItems():
            try:
                p = item.Path
            except Exception:
                continue  # 虚拟项（此电脑/回收站等）取不到路径
            if p:
                paths.append(str(p))
        return paths
    except Exception as e:
        # 不能当成"没选中"吞掉：调用方据此提示真实原因
        raise SelectionError(f"读取资源管理器选中项失败：{type(e).__name__}: {e}") from e
    finally:
        if should_uninit:
            ole32.CoUninitialize()


class SelectionError(Exception):
    """COM 调用失败等（区别于"前台不是资源管理器/没选中"）。"""


_SID_STopLevelBrowser = "{4C96BE40-915C-11CF-99D3-00AA004AE837}"


def _active_tab(windows: list):
    """多个候选（同一窗口的多个标签）里返回可见的那个；判断不了就返回第一个。"""
    if len(windows) <= 1:
        return windows[0] if windows else None
    try:
        import pythoncom
        from win32com.shell import shell as _shell

        for w in windows:
            try:
                sp = w._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
                browser = sp.QueryService(_SID_STopLevelBrowser, _shell.IID_IShellBrowser)
                tab_hwnd = browser.GetWindow()
                if tab_hwnd and user32.IsWindowVisible(tab_hwnd):
                    return w
            except Exception:
                continue
    except Exception:
        pass
    return windows[0]
