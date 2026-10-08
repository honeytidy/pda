# -*- coding: utf-8 -*-
"""自动升级：查 GitHub Releases 最新版本 → 下载安装包 → 静默覆盖安装。

更新源：https://api.github.com/repos/honeytidy/pda/releases/latest（公开仓库，无需 token）。
取 release 里第一个 .exe 资源作为安装包；GitHub 为资源提供的 sha256 digest 用来校验下载完整性
（只防传输损坏，不防发布源被篡改——未做签名校验）。

安装包是 Inno Setup per-user 安装（PrivilegesRequired=lowest），覆盖安装即升级、无需 UAC；
带 /RELAUNCH 时安装完成后自动重新启动程序（见 packaging/installer.iss）。
便携版 / 源码运行不能用安装包覆盖，只提示并打开下载页。

网络请求必须在 worker 线程调用（本模块不做线程管理，也不依赖 Qt）。
"""
import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import __version__, config

REPO = "honeytidy/pda"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

_UA = f"PDA-Updater/{__version__}"
_TIMEOUT = 15
_MAX_BYTES = 1024 * 1024 * 1024   # 安装包大小上限（防止异常资源把磁盘写满）
_DOWNLOAD_DEADLINE = 60 * 60      # 整次下载总时长上限


class UpdateError(Exception):
    """检查或下载更新失败（给用户看的友好信息）。"""


def parse_version(text: str) -> tuple:
    """'v0.2.10' / '0.2.10-beta' → (0, 2, 10)；解析不出数字返回 ()。"""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(remote: str, local: str = __version__) -> bool:
    r, l = parse_version(remote), parse_version(local)
    if not r:
        return False
    width = max(len(r), len(l))
    return r + (0,) * (width - len(r)) > l + (0,) * (width - len(l))


def can_self_install() -> bool:
    """只有安装版能用安装包覆盖升级；便携版 / 源码运行只提示去下载页。"""
    return config.FROZEN and not config.PORTABLE


def check_latest() -> dict | None:
    """有新版本时返回 {version, notes, url, name, size, sha256, page}，否则 None。失败抛 UpdateError。"""
    import requests  # 延迟导入：不拖慢窗口首次显示

    try:
        resp = requests.get(LATEST_API, timeout=_TIMEOUT, headers={
            "User-Agent": _UA, "Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        raise UpdateError(f"无法连接 GitHub：{type(e).__name__}")
    if resp.status_code == 404:
        return None  # 还没有发布过正式版本
    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        raise UpdateError("GitHub 接口访问次数已达上限，请稍后再试")
    if resp.status_code >= 400:
        raise UpdateError(f"检查更新失败（HTTP {resp.status_code}）")
    try:
        data = resp.json()
    except ValueError:
        raise UpdateError("检查更新失败：GitHub 返回内容无法解析")
    if data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name") or "")
    if not is_newer(version):
        return None
    asset = next((a for a in data.get("assets") or []
                  if str(a.get("name", "")).lower().endswith(".exe")), None)
    if asset is None:
        raise UpdateError(f"新版本 {version} 没有可用的安装包")
    digest = str(asset.get("digest") or "")
    return {
        "version": version.lstrip("vV"),
        "notes": str(data.get("body") or "").strip(),
        "url": asset.get("browser_download_url"),
        "name": asset.get("name"),
        "size": int(asset.get("size") or 0),
        "sha256": digest[7:].lower() if digest.startswith("sha256:") else "",
        "page": data.get("html_url") or RELEASES_PAGE,
    }


def download(info: dict, progress=None, cancelled=None) -> str:
    """下载安装包到临时目录并校验 sha256，返回本地路径。

    progress(done_bytes, total_bytes) 用于报告进度；cancelled() 返回 True 时中止。
    """
    import requests

    total = info.get("size") or 0
    if total > _MAX_BYTES:
        raise UpdateError("安装包大小异常，已放弃下载")
    folder = Path(tempfile.gettempdir()) / "pda_update"
    folder.mkdir(parents=True, exist_ok=True)
    # 资源名来自网络：只取文件名部分，去掉非法字符
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(str(info.get("name") or "")).name)
    if not name.lower().endswith(".exe"):
        name = f"pda-setup-{info.get('version', 'new')}.exe"
    target = folder / name
    part = target.with_name(target.name + ".part")
    sha = hashlib.sha256()
    done = 0
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE
    try:
        with requests.get(info["url"], stream=True, timeout=_TIMEOUT,
                          headers={"User-Agent": _UA}) as resp:
            if resp.status_code >= 400:
                raise UpdateError(f"下载失败（HTTP {resp.status_code}）")
            total = total or int(resp.headers.get("Content-Length") or 0)
            with open(part, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if cancelled is not None and cancelled():
                        raise UpdateError("已取消下载")
                    if time.monotonic() > deadline:
                        raise UpdateError("下载超时")
                    done += len(chunk)
                    if done > _MAX_BYTES:
                        raise UpdateError("安装包大小异常，已放弃下载")
                    f.write(chunk)
                    sha.update(chunk)
                    if progress is not None:
                        progress(done, total)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"下载失败：{type(e).__name__}")
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if total and done != total:
        part.unlink(missing_ok=True)
        raise UpdateError("下载不完整，请重试")
    expected = info.get("sha256")
    if expected and sha.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("安装包校验失败（sha256 不一致），请重试")
    os.replace(part, target)
    return str(target)


def launch_installer(path: str):
    """静默安装并在完成后重启程序；调用方随后应正常退出，让安装程序替换文件。"""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH"],
                     creationflags=flags, close_fds=True)
