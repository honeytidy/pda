# -*- coding: utf-8 -*-
"""个人助理知识库 — GUI 入口。

用法：
  python run.py                  启动主窗口
  python run.py --minimized      启动后直接最小化到托盘（开机自启用）
  python run.py --quit           让运行中的主程序正常退出（卸载程序用），最多等 10 秒
  python run.py --add <路径...>   收录文件/文件夹（右键菜单"添加到知识库助理"用）：
                                 转发给运行中的主程序；主程序没开时先最小化拉起它再转发，
                                 全程只有主程序一个进程写库

注意：右键菜单用 pythonw.exe 调用本脚本，此时没有控制台（sys.stdout 为 None），
任何 print 都会崩，所以所有输出走 _safe_print，未捕获异常写进 data/pda_error.log。
"""
import os
import sys
import traceback

# pythonw / windowed exe 下 sys.stdout、sys.stderr 为 None：print 会崩，
# 第三方库（tqdm 进度条等）写 stdout 也会崩。统一重定向到 devnull 兜底。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")

APP_TITLE = "个人助理知识库"


def _safe_print(msg):
    """pythonw 下 sys.stdout 为 None，print 会 AttributeError。"""
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:
        pass


def _log_crash(exc_type, exc_value, exc_tb):
    """未捕获异常写日志文件（pythonw 无控制台，否则静默死亡无从排查）。"""
    try:
        from pda import config

        config.ensure_dirs()
        log_path = config.ERROR_LOG
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                + "\n"
            )
    except Exception:
        pass


def _error_log_path() -> str:
    try:
        from pda import config

        return str(config.ERROR_LOG)
    except Exception:
        return "pda_error.log"


def _thread_crash(args):
    _log_crash(args.exc_type, args.exc_value, args.exc_traceback)


def _native_message(text: str, error: bool = True):
    """不依赖 Qt 的消息框（Qt 未初始化 / 客户端进程不想拉起 Qt 时用）。"""
    try:
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, flags | 0x40000)  # MB_TOPMOST
    except Exception:
        _safe_print(text)


def _client_toast(text: str, success: bool = False):
    """转发失败时的轻量反馈：拉起一个极简 Qt toast，显示约 3 秒后退出。"""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from pda.ui.toast import show_toast

        app = QApplication.instance() or QApplication(sys.argv[:1])
        show_toast(text, success=success)
        QTimer.singleShot(3200, app.quit)
        app.exec()
    except Exception:
        _native_message(text, error=not success)


def _run_add(paths) -> int:
    from pda import ipc

    # 相对路径按本进程工作目录转成绝对路径（主程序工作目录与本进程不同）
    msg = {"paths": [os.path.abspath(str(p)) for p in paths]}

    def forward(total_sec):
        """转发给运行中的实例。返回 True=已被接受；str=被拒绝的原因；None=没转发成功。"""
        import time

        deadline = time.monotonic() + total_sec
        while True:
            reply = ipc.send(msg)
            if reply == "ok":
                return True
            if reply and reply.startswith("invalid:"):
                return reply[len("invalid:"):]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.3)

    r = forward(0)
    if r is True:
        _safe_print("已发送给运行中的知识库助理，正在收录")
        return 0
    if isinstance(r, str):
        _client_toast(r)
        return 1
    if ipc.running_in_other_session():
        _client_toast("知识库助理正在另一个 Windows 登录会话中运行，无法在这里收录")
        return 1
    # 主程序没开（或正在退出）：最小化拉起主程序（只有它一个进程写库），等它 IPC 就绪后转发。
    # 多选右键会同时起多个本进程：spawn_guard 保证只有一个去拉起，其余只等待转发。
    import time

    guard = ipc.spawn_guard()
    try:
        deadline = time.monotonic() + 60  # 主程序冷启动约 5-6 秒，慢机器 / 首次解压更久
        last_launch = None
        while time.monotonic() < deadline:
            r = forward(0)
            if r is True:
                return 0
            if isinstance(r, str):
                _client_toast(r)
                return 1
            # 没有实例在跑：拉起（正在退出的旧实例释放锁后也会走到这里）；拉起后 20 秒还没起来就再试一次
            if guard is not None and not ipc.instance_running() and (
                last_launch is None or time.monotonic() - last_launch > 20
            ):
                _launch_main_minimized()
                last_launch = time.monotonic()
            time.sleep(0.3)
    finally:
        ipc.release_spawn_guard(guard)
    _client_toast("知识库助理启动超时，本次收录未完成，请打开知识库助理后重试")
    return 1


def _run_quit() -> int:
    """请求运行中的实例正常退出并等它释放单实例锁。没有实例在跑也返回 0。"""
    import time

    from pda import ipc

    if not ipc.instance_running():
        return 0
    ipc.send({"action": "quit"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not ipc.instance_running():
            return 0
        time.sleep(0.2)
    return 1  # 没退出：卸载程序会退回强制结束


def _launch_main_minimized():
    """后台启动主程序（托盘常驻，不弹主窗口）。"""
    import subprocess

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--minimized"]
    else:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pythonw if os.path.isfile(pythonw) else sys.executable
        cmd = [exe, os.path.abspath(__file__), "--minimized"]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main():
    from pda import ipc

    if "--quit" in sys.argv:
        return _run_quit()

    # 单实例由命名互斥体 + 数据目录文件锁保证（在 import 重依赖之前抢占）：同一时间只有一个进程
    # 打开 SQLite/chroma。拿不到锁说明已有实例（可能还在启动中，IPC 未就绪），
    # 这时重试转发而不是再起一个实例。

    # --add 模式：转发给运行中的主程序（秒退、无窗口）；主程序没开则先最小化拉起再转发
    if "--add" in sys.argv:
        paths = [a for a in sys.argv[1:] if a not in ("--add", "--minimized")]
        if not paths:
            _safe_print("用法：python run.py --add <文件或文件夹路径>")
            return 2
        return _run_add(paths)

    minimized = "--minimized" in sys.argv

    # 无参二次启动：主程序已运行则激活它并退出。
    # 本进程是用户刚启动的前台进程，先授权他人抢前台，否则对方 activateWindow 只会闪任务栏
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass
    if minimized:
        # 开机自启 / 右键拉起：已有实例就什么都不做（不能把用户藏在托盘里的窗口弹出来）
        if not ipc.acquire_instance_lock():
            return 0
    else:
        if ipc.send({"action": "activate"}) == "ok":
            return 0
        if not ipc.acquire_instance_lock():
            # 主程序正在启动：等它的 IPC 就绪后激活
            if ipc.send_message_retry({"action": "activate"}, total_sec=30):
                return 0
            if ipc.running_in_other_session():
                _native_message("知识库助理已在另一个 Windows 登录会话中运行（同一数据目录只能运行一个）。")
            else:
                _native_message("知识库助理正在启动或无响应，请稍后再试。")
            return 0

    # 125%/150% 等分数缩放下按整数取整会导致字体模糊，PassThrough 保持原生缩放比例
    # （必须在创建 QApplication 之前设置；--add / --quit 客户端进程不加载 Qt 主界面）
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    # 字体规范见 pda/ui/theme.py：Times New Roman（西文/数字）+ 宋体（中文）
    from pda.ui.theme import app_font

    app.setFont(app_font())

    # 启动 splash：尽早出现（重依赖 import 在之后），--add/IPC 转发路径不经过这里；
    # 由原生启动器 pda.exe 拉起时（PDA_NO_INTERNAL_SPLASH=1）跳过——外部 splash 已显示；
    # 开机自启（--minimized）也不显示
    splash = None
    if not os.environ.get("PDA_NO_INTERNAL_SPLASH") and not minimized:
        from pda.ui.splash import SplashScreen

        splash = SplashScreen()
        splash.show()
        splash.show_status("正在初始化…")
        splash.show_status("正在加载组件…")

    try:
        from pda.ui.main_window import MainWindow

        win = MainWindow(splash=splash)
    except Exception as e:
        # 构造期致命错误（数据库初始化失败、依赖缺失等）：pythonw/exe 下必须让用户看得到
        _log_crash(*sys.exc_info())
        if splash is not None:
            splash.close()
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            APP_TITLE,
            f"启动失败：{type(e).__name__}: {e}\n\n详细信息已写入 {_error_log_path()}",
        )
        return 1
    win.resize(1100, 720)
    if splash is not None:
        splash.show_status("就绪")
    if minimized and win.tray is not None:
        win.start_in_tray()
    else:
        win.showMaximized()  # resize 的 1100x720 作为还原后的窗口尺寸
    if splash is not None:
        splash.finish(win)
    return app.exec()


if __name__ == "__main__":
    import threading

    sys.excepthook = _log_crash
    threading.excepthook = _thread_crash
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _log_crash(*sys.exc_info())
        sys.exit(1)
