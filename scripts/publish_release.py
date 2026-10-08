# -*- coding: utf-8 -*-
"""把安装包发布到 GitHub Releases，供程序内自动升级（pda/updater.py）使用。

用法：
  python scripts/build_exe.py --installer     # 先打安装包
  python scripts/publish_release.py [--notes 更新说明.md] [--draft]

需要已登录的 GitHub CLI（gh auth login）。版本号取 pda/__init__.py 的 __version__，
tag 为 v<版本>；上传的资源名 pda-v<版本>.exe（updater 取 release 里第一个 .exe）。
GitHub 会为资源自动计算 sha256 digest，客户端下载后用它校验完整性。
正式发布成功后自动把 README 的"下载最新版"链接换成新版本，并提交、推送到当前分支；
--readme-only 只做这一步（例如草稿在网页上转正式之后）。
"""
import argparse
import re
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
    ap.add_argument("--readme-only", action="store_true",
                    help="不发布，只把 README 下载链接更新为当前版本并提交推送")
    args = ap.parse_args()
    if args.readme_only:
        update_readme(f"v{app_version()}")
        return

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
    if args.draft:
        print("草稿版本：README 下载链接未更新（正式发布后再跑一次 --readme-only）")
    else:
        update_readme(tag)


# README "下载最新版" 链接：releases/download/<tag>/pda-<tag>.exe
_README_LINK = re.compile(
    r"\[⬇ 下载最新版（v[\d.]+）\]\(https://github\.com/" + re.escape(REPO)
    + r"/releases/download/v[\d.]+/pda-v[\d.]+\.exe\)")


def update_readme(tag: str):
    """release 发布成功后把 README 下载链接换成新版本，并提交、推送到当前分支。

    放在发布之后：链接先指过去，资源还没上传就是 404。
    """
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    new_link = (f"[⬇ 下载最新版（{tag}）](https://github.com/{REPO}"
                f"/releases/download/{tag}/pda-{tag}.exe)")
    new_text, n = _README_LINK.subn(new_link, text)
    if n == 0:
        print("警告：README 里没找到“下载最新版”链接，未更新")
        return
    if new_text == text:
        print("README 下载链接已是最新")
        return
    readme.write_text(new_text, encoding="utf-8")
    git = ["git", "-C", str(ROOT)]
    subprocess.run(git + ["add", "README.md"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", f"README 下载链接指向 {tag}"], check=True)
    subprocess.run(git + ["push", "-q"], check=True)
    print(f"README 下载链接已更新为 {tag} 并推送")


if __name__ == "__main__":
    main()
