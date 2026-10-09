# -*- coding: utf-8 -*-
"""把安装包发布到 GitHub Releases，供程序内自动升级（pda/updater.py）使用。

用法：
  python scripts/build_exe.py --installer     # 先打安装包
  python scripts/publish_release.py [--notes 更新说明.md] [--draft]

需要已登录的 GitHub CLI（gh auth login）。版本号取 pda/__init__.py 的 __version__，
tag 为 v<版本>；上传的资源名 pda-v<版本>.exe（updater 取 release 里第一个 .exe）。
GitHub 会为资源自动计算 sha256 digest，客户端下载后用它校验完整性。
正式发布成功后：
  1. 同步到国内镜像（阿里云 ECS，https://aitool.center/pda/）：scp 上传安装包，最后写 latest.json，
     只保留最近 KEEP_VERSIONS 个安装包。需要本机 ssh 密钥能登录 MIRROR_HOST。
  2. 把 README 的"下载最新版"链接换成新版本，并提交、推送到当前分支。
--mirror-only 只做第 1 步、--readme-only 只做第 2 步（例如草稿在网页上转正式之后）。
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "honeytidy/pda"
MIRROR_HOST = "root@39.107.106.195"
MIRROR_DIR = "/home/work/pda"
KEEP_VERSIONS = 3
SITE_DIR = ROOT / "site"   # 镜像首页模板和图片


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
    ap.add_argument("--mirror-only", action="store_true",
                    help="不发布，只把当前版本安装包同步到国内镜像")
    args = ap.parse_args()
    if args.readme_only:
        update_readme(f"v{app_version()}")
        return
    if args.mirror_only:
        sync_mirror(f"v{app_version()}", args.notes)
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
        print("草稿版本：镜像和 README 未更新（正式发布后再跑 --mirror-only 和 --readme-only）")
    else:
        sync_mirror(tag, args.notes)
        update_readme(tag)


def sync_mirror(tag: str, notes_file: str | None = None):
    """把安装包、latest.json 和首页传到国内镜像。latest.json 最后改名：客户端看到新版本时安装包已经就位。"""
    version = tag.lstrip("v")
    installer = ROOT / "dist" / f"知识库助理_安装包_{version}.exe"
    if not installer.is_file():
        sys.exit(f"错误：未找到 {installer}")
    name = f"pda-{tag}.exe"
    sha = hashlib.sha256()
    with open(installer, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    notes = Path(notes_file).read_text(encoding="utf-8") if notes_file else ""
    latest = {
        "version": version,
        "name": name,
        "size": installer.stat().st_size,
        "sha256": sha.hexdigest(),
        "notes": notes,
        "github_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
        "page": f"https://github.com/{REPO}/releases/tag/{tag}",
    }
    ssh = ["ssh", "-o", "BatchMode=yes", MIRROR_HOST]
    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "latest.json"
        meta.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
        page = Path(tmp) / "index.html"
        page.write_text(download_page(latest), encoding="utf-8")
        print(f"上传到镜像 {MIRROR_HOST}:{MIRROR_DIR}/{name}")
        # 先传 .tmp 再改名：上传中途失败不会留下半截的正式文件
        subprocess.run(["scp", "-o", "BatchMode=yes", str(installer),
                        f"{MIRROR_HOST}:{MIRROR_DIR}/{name}.tmp"], check=True)
        for f in (meta, page):
            subprocess.run(["scp", "-o", "BatchMode=yes", str(f),
                            f"{MIRROR_HOST}:{MIRROR_DIR}/{f.name}.tmp"], check=True)
    # 首页图片、演示视频（文件不大，每次全量覆盖）
    subprocess.run(ssh + [f"mkdir -p {MIRROR_DIR}/assets"], check=True)
    subprocess.run(["scp", "-o", "BatchMode=yes", "-q",
                    *[str(f) for f in sorted((SITE_DIR / "assets").iterdir()) if f.is_file()],
                    f"{MIRROR_HOST}:{MIRROR_DIR}/assets/"], check=True)
    # 安装包就位后再替换 latest.json / 下载页；只保留最近几个版本的安装包
    remote = (f"cd {MIRROR_DIR} && mv -f {name}.tmp {name}"
              f" && mv -f latest.json.tmp latest.json && mv -f index.html.tmp index.html"
              f" && chmod 644 {name} latest.json index.html assets/*"
              f" && ls -t pda-v*.exe | tail -n +{KEEP_VERSIONS + 1} | xargs -r rm -f")
    subprocess.run(ssh + [remote], check=True)
    print(f"镜像已更新：https://aitool.center/pda/{name}")


def download_page(latest: dict) -> str:
    """镜像首页 https://aitool.center/pda/：用 site/index.html 模板填入版本信息（纯静态，无脚本）。"""
    from html import escape

    values = {
        "VERSION": latest["version"],
        "FILE": latest["name"],
        "SIZE_MB": f"{latest['size'] / 1024 / 1024:.0f}",
        "SHA256": latest["sha256"],
        "NOTES": latest.get("notes") or "本次为常规更新。",
        "GITHUB_URL": latest["github_url"],
        "RELEASES_PAGE": f"https://github.com/{REPO}/releases",
    }
    html = (SITE_DIR / "index.html").read_text(encoding="utf-8")
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", escape(str(value)))
    if "{{" in html:
        sys.exit("错误：site/index.html 里有未替换的占位符")
    return html


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
