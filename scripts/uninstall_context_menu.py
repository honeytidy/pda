# -*- coding: utf-8 -*-
"""卸载 Windows 右键菜单"添加到知识库助理"（删除 install_context_menu.py 写入的键）。

同时清理旧版本遗留：键名 AddToPDA、HKCU\\Software\\PDA\\InstallPath（旧版 Win11 原生菜单用）。

不会动（需要时手动处理）：
  - 开机自启（主程序"设置"里取消勾选即可）
  - 旧版装过的 Win11 原生菜单 MSIX 包：
    powershell "Get-AppxPackage -Name PDA.KnowledgeAssistant | Remove-AppxPackage"
"""
import sys
import winreg

# 当前键名 + 旧版遗留键名
KEY_NAMES = ["0AddToPDA", "AddToPDA"]

PDA_KEY = r"Software\PDA"

TARGETS = [
    r"Software\Classes\*\shell",
    r"Software\Classes\Directory\shell",
]


def delete_tree(root, path: str) -> None:
    """递归删除注册表键（winreg.DeleteKey 不能删带子键的键）。"""
    with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            delete_tree(root, f"{path}\\{sub}")
    winreg.DeleteKey(root, path)


def main():
    if sys.platform != "win32":
        print("仅支持 Windows")
        return 1
    removed = 0
    for parent_path in TARGETS:
        for name in KEY_NAMES:
            key_path = f"{parent_path}\\{name}"
            try:
                delete_tree(winreg.HKEY_CURRENT_USER, key_path)
                print(f"已删除 HKCU\\{key_path}")
                removed += 1
            except FileNotFoundError:
                pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PDA_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, "InstallPath")
        print(f"已删除 HKCU\\{PDA_KEY}\\InstallPath")
        removed += 1
    except FileNotFoundError:
        pass
    # 键已空时顺手删掉（DeleteKey 对有子键的键会失败，有其他内容就保留）
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PDA_KEY) as key:
            n_sub, n_val, _ = winreg.QueryInfoKey(key)
        if n_sub == 0 and n_val == 0:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, PDA_KEY)
    except OSError:
        pass
    print("完成。" if removed else "没有找到已安装的菜单项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
