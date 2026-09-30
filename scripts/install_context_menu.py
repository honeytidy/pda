# -*- coding: utf-8 -*-
"""安装 Windows 右键菜单"添加到知识库助理"（HKCU，per-user，无需管理员）。

在"所有文件"和"文件夹"的右键菜单注册命令：
  打包版（frozen）：<sys.executable> --add "%1"
  源码运行：        <pythonw.exe> <run.py> --add "%1"
  显式指定：        python scripts/install_context_menu.py --exe <path\\to\\main.exe>
卸载：python scripts/uninstall_context_menu.py
"""
import argparse
import sys
import winreg
from pathlib import Path

MENU_NAME = "添加到知识库助理"
# 经典菜单按动词键名字母序排列，数字前缀让我们排在其他自定义项之前
KEY_NAME = "0AddToPDA"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_PY = PROJECT_ROOT / "run.py"

# 目标键：*（所有文件）与 Directory（文件夹）
TARGETS = [
    r"Software\Classes\*\shell",
    r"Software\Classes\Directory\shell",
]


def resolve_exe(exe: str | None) -> Path | None:
    """要注册的 main.exe：显式 --exe > 打包运行时自身 exe > None（源码运行）。

    不自动探测 dist/pda/main.exe：避免把开发机构建目录写进注册表。
    """
    if exe:
        p = Path(exe).resolve()
        if not p.is_file():
            raise SystemExit(f"--exe 指定的文件不存在：{p}")
        return p
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def command_line(exe: Path | None) -> str:
    if exe:
        return f'"{exe}" --add'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    py = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{py}" "{RUN_PY}" --add'


def main():
    if sys.platform != "win32":
        print("仅支持 Windows")
        return 1
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", help="main.exe 的路径（不指定则按运行方式自动选择）")
    args = ap.parse_args()

    exe = resolve_exe(args.exe)
    cmd = f'{command_line(exe)} "%1"'
    for parent_path in TARGETS:
        key_path = f"{parent_path}\\{KEY_NAME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, MENU_NAME)
            # MultiSelectModel=Player：去掉经典菜单"多选超过 15 项就隐藏"的限制。
            # 注意 Explorer 仍会每项起一个进程（"%1" 只有一个路径），各自经 IPC
            # 转给主程序排队后秒退。大批量（上百项）建议直接拖进主窗口。
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, cmd)
        print(f"已写入 HKCU\\{key_path}")
    print(f"\n命令行：{cmd}")
    print("完成。")
    print("注意：Windows 11 的新版右键菜单会把本项折叠到“显示更多选项”里")
    print("（或按住 Shift 再右键）。如需让本项直接出现在顶级菜单，可运行：")
    print("  python scripts/restore_classic_menu.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
