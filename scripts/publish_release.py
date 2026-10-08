# -*- coding: utf-8 -*-
"""把安装包发布到 GitHub Releases，供程序内自动升级（pda/updater.py）使用。

用法：
  python scripts/build_exe.py --installer     # 先打安装包
  python scripts/publish_release.py [--notes 更新说明.md] [--draft]

需要已登录的 GitHub CLI（gh auth login）。版本号取 pda/__init__.py 的 __version__，
tag 为 v<版本>；上传的资源名 pda-v<版本>.exe（updater 取 release 里第一个 .exe）。
GitHub 会为资源自动计算 sha256 digest，客户端下载后用它校验完整性。
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "honeytidy/pda"


def app_version() -> str:
    ns = {}
    exec((ROOT / "pda" / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"]


def main():
    ap = argparse.ArgumentParser(description="发布安装包到 GitHub Releases")
    ap.add_argument("--notes", help="更新说明文件（Markdown），会显示在程序的升级提示里")
    ap.add_argument("--draft", action="store_true", help="先建草稿，确认后在网页上发布")
    args = ap.parse_args()

    if shutil.which("gh") is None:
        sys.exit("错误：未找到 GitHub CLI（gh），请先安装并执行 gh auth login")
    version = app_version()
    tag = f"v{version}"
    installer = ROOT / "dist" / f"知识库助理_安装包_{version}.exe"
    if not installer.is_file():
        sys.exit(f"错误：未找到 {installer}，先跑 python scripts/build_exe.py --installer")

    # 资源名用 ASCII：中文文件名在 GitHub 下载链接里会被改写
    with tempfile.TemporaryDirectory() as tmp:
        asset = Path(tmp) / f"pda-{tag}.exe"
        shutil.copy2(installer, asset)
        cmd = ["gh", "release", "create", tag, str(asset), "-R", REPO, "--title", tag]
        if args.notes:
            cmd += ["--notes-file", args.notes]
        else:
            cmd += ["--notes", ""]
        if args.draft:
            cmd.append("--draft")
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)
    print(f"已发布 {tag}：https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
