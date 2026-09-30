# -*- coding: utf-8 -*-
"""Windows 11 经典右键菜单开关。

Win11 的新版右键菜单默认只显示应用注册的现代化菜单项，"添加到知识库助理"
这类传统注册表项被折叠到"显示更多选项"（或 Shift+右键）里。

本脚本通过写入/删除 CLSID {86ca1aa0-34aa-4e8b-a509-50c905bae2a2} 占位键，
让资源管理器恢复 Windows 10 风格的经典右键菜单（所有右键菜单都会变回经典样式，
"添加到知识库助理"等项直接出现在顶级）。随时可用 --off 还原。

用法：
  python scripts/restore_classic_menu.py          # 启用经典菜单
  python scripts/restore_classic_menu.py --off    # 还原为 Win11 新版菜单

切换后需要重启资源管理器生效（脚本会询问是否立即重启，默认不重启；任务栏会闪一下）。
"""
import ctypes
import sys
import winreg

CLSID_PATH = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"


def is_enabled() -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLSID_PATH + r"\InprocServer32").Close()
        return True
    except FileNotFoundError:
        return False


def enable():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CLSID_PATH + r"\InprocServer32") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "")
    print("已启用经典右键菜单。")


def disable():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CLSID_PATH + r"\InprocServer32")
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CLSID_PATH)
    except FileNotFoundError:
        pass
    except OSError as e:
        # CLSID 键下还有别的子键（其他工具写的）：InprocServer32 已删，开关已生效
        print(f"提示：{CLSID_PATH} 下还有其他子键，已保留（{e}）")
    print("已还原为 Win11 新版右键菜单。")


def restart_explorer():
    try:
        answer = input("立即重启资源管理器使其生效？[y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer in ("y", "yes") and sys.stdin.isatty():
        ctypes.windll.shell32.ShellExecuteW(
            None, "open", "cmd.exe",
            "/c taskkill /f /im explorer.exe & start explorer.exe",
            None, 0,
        )
        print("资源管理器已重启。")
    else:
        print("请重启资源管理器（任务管理器 → Windows 资源管理器 → 重新启动）或注销重登使其生效。")


def main():
    off = "--off" in sys.argv
    if off:
        disable()
    else:
        enable()
    print(f"当前状态：{'经典菜单' if is_enabled() else 'Win11 新版菜单'}")
    restart_explorer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
