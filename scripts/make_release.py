# -*- coding: utf-8 -*-
"""制作发行包：把 dist/pda 打成 zip，内置免 Python 的右键菜单注册/卸载 bat。

用法：python scripts/make_release.py
产物：dist/知识库助理_portable.zip

用户数据绝不进包：dist/pda/data/ 下只保留 model_cache/（内置语义模型，
省去首次启动下载），pda.db / chroma / files / notes 等全部排除；
pda_config.json（可能含 API key）与 *.log 也排除。
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "pda"
OUT = ROOT / "dist" / "知识库助理_portable.zip"

# data/ 下允许打进包的子目录（相对 data/）
DATA_KEEP = ("model_cache",)
EXCLUDE_NAMES = {"pda_config.json"}
EXCLUDE_SUFFIXES = {".log"}


def should_pack(rel: Path) -> bool:
    """rel 为相对 dist/pda 的路径。"""
    if rel.name in EXCLUDE_NAMES or rel.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    parts = rel.parts
    if parts and parts[0].lower() == "data":
        return len(parts) >= 3 and parts[1] in DATA_KEEP
    return True


def model_bundled() -> bool:
    mc = DIST / "data" / "model_cache"
    return mc.is_dir() and any(f.is_file() for f in mc.rglob("*"))


# 右键菜单注册三处保持一致（键名 0AddToPDA、默认值、Icon、MultiSelectModel、command）：
# 本文件的 bat（便携版，免 Python）、packaging/installer.iss、scripts/install_context_menu.py。
# bat 统一 UTF-8（无 BOM）+ 首行 chcp 65001：不依赖系统代码页（英文系统 / "UTF-8 Beta"
# 下 GBK bat 的中文会写成乱码）。chcp 必须在任何非 ASCII 行之前；BOM 会破坏第一行。
INSTALL_BAT = r"""@echo off
chcp 65001 >nul
rem 注册右键菜单"添加到知识库助理"（HKCU，无需管理员）
set "EXE=%~dp0main.exe"
if not exist "%EXE%" (
  echo "未找到 %EXE%"
  pause
  exit /b 1
)
rem MultiSelectModel=Player：去掉多选超过 15 项隐藏菜单的限制（仍每项一个进程，各自转发给主程序后秒退）
reg add "HKCU\Software\Classes\*\shell\0AddToPDA" /ve /d "添加到知识库助理" /f >nul
reg add "HKCU\Software\Classes\*\shell\0AddToPDA" /v Icon /d "\"%EXE%\",0" /f >nul
reg add "HKCU\Software\Classes\*\shell\0AddToPDA" /v MultiSelectModel /d "Player" /f >nul
reg add "HKCU\Software\Classes\*\shell\0AddToPDA\command" /ve /d "\"%EXE%\" --add \"%%1\"" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\0AddToPDA" /ve /d "添加到知识库助理" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\0AddToPDA" /v Icon /d "\"%EXE%\",0" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\0AddToPDA" /v MultiSelectModel /d "Player" /f >nul
reg add "HKCU\Software\Classes\Directory\shell\0AddToPDA\command" /ve /d "\"%EXE%\" --add \"%%1\"" /f >nul
echo 完成。右键任意文件/文件夹即可看到"添加到知识库助理"。
echo （Windows 11 请在右键菜单点"显示更多选项"，或按住 Shift 右键）
pause
"""

UNINSTALL_BAT = r"""@echo off
chcp 65001 >nul
reg delete "HKCU\Software\Classes\*\shell\0AddToPDA" /f >nul 2>&1
reg delete "HKCU\Software\Classes\Directory\shell\0AddToPDA" /f >nul 2>&1
rem 旧版遗留：键名 AddToPDA、原生菜单用的 InstallPath
reg delete "HKCU\Software\Classes\*\shell\AddToPDA" /f >nul 2>&1
reg delete "HKCU\Software\Classes\Directory\shell\AddToPDA" /f >nul 2>&1
reg delete "HKCU\Software\PDA" /v InstallPath /f >nul 2>&1
reg query "HKCU\Software\PDA" /s 2>nul | findstr /c:"REG_" >nul || reg delete "HKCU\Software\PDA" /f >nul 2>&1
echo 右键菜单已卸载。
pause
"""

README_TXT = """知识库助理 —— 绿色便携版
========================

【运行】双击 pda.exe（{model_note}）
【数据】所有数据保存在本目录的 data/ 文件夹，整体拷贝本目录即可迁移
【右键菜单】（可选）双击"安装右键菜单.bat"，之后右键文件即可收录；
            卸载用"卸载右键菜单.bat"
【快捷键】主程序运行时：Ctrl+Shift+A 收录资源管理器选中的文件；
          Ctrl+Shift+Q 把剪贴板文字存为笔记；
          Ctrl+Alt+Space 随时呼出/隐藏主界面
【AI 问答】（可选）主窗口右下角"设置"里填写 OpenAI 兼容接口的 API Key / 地址 / 模型。
  不配置也能用：提问会返回检索到的原文片段和出处。
  配置后，提问时检索到的片段、收录时文档前 1500 字（自动标签，可在设置里关闭）会发送给该服务商。
"""


def main():
    if not ((DIST / "pda.exe").is_file() and (DIST / "main.exe").is_file()):
        raise SystemExit("dist/pda 不完整，先跑 scripts/build_exe.py")
    if not model_bundled():
        # build_exe.py 会放入模型；缺了说明构建不完整，不能打出首次启动还要下载的包
        raise SystemExit("dist/pda/data/model_cache 里没有语义模型，先跑 scripts/build_exe.py")

    (DIST / "安装右键菜单.bat").write_text(
        INSTALL_BAT, encoding="utf-8", newline="\r\n")
    (DIST / "卸载右键菜单.bat").write_text(
        UNINSTALL_BAT, encoding="utf-8", newline="\r\n")
    model_note = "语义模型已内置，无需联网下载"
    (DIST / "使用说明.txt").write_text(
        README_TXT.replace("{model_note}", model_note),
        encoding="utf-8", newline="\r\n")

    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        skipped = 0
        for f in sorted(DIST.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(DIST)
            if not should_pack(rel):
                skipped += 1
                continue
            z.write(f, Path("知识库助理") / rel)
        notices = ROOT / "THIRD_PARTY_NOTICES.txt"
        if notices.is_file():
            z.write(notices, Path("知识库助理") / notices.name)
        else:
            print("警告：缺少 THIRD_PARTY_NOTICES.txt，先跑 scripts/make_notices.py")
        # 便携标记：有它时数据写在 exe 旁 data/，没有则按安装版写 %LOCALAPPDATA%\PDA（见 pda/config.py）
        z.writestr(str(Path("知识库助理") / "portable.flag"),
                   "此文件表示便携版：数据保存在本目录 data/ 下。删除后数据改存 %LOCALAPPDATA%\\PDA。\r\n")
    print(f"已排除 {skipped} 个用户数据/配置/日志文件")
    print(f"发行包：{OUT}（{OUT.stat().st_size / 1024 / 1024:.0f} MB）")


if __name__ == "__main__":
    main()
