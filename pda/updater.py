# -*- coding: utf-8 -*-
"""自动升级：查最新版本 → 下载安装包 → 静默覆盖安装。

更新源（都是 HTTPS）：
  1. 国内镜像 https://aitool.center/pda/latest.json（阿里云 ECS，scripts/publish_release.py 发布时同步）
  2. GitHub https://api.github.com/repos/honeytidy/pda/releases/latest（镜像不通时回退）
国内不翻墙访问 GitHub 下载经常很慢或断流，所以检查、下载都先走镜像；下载失败再换 GitHub。
下载支持断点续传（HTTP Range）并自动重试；sha256 用来校验下载完整性
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
MIRROR_BASE = "https://aitool.center/pda/"
MIRROR_LATEST = MIRROR_BASE + "latest.json"

_UA = f"PDA-Updater/{__version__}"
_TIMEOUT = 15
_MIRROR_TIMEOUT = 8               # 镜像不通时尽快回退 GitHub
_MAX_BYTES = 1024 * 1024 * 1024   # 安装包大小上限（防止异常资源把磁盘写满）
_DOWNLOAD_DEADLINE = 60 * 60      # 整次下载总时长上限
_RETRIES = 3                      # 每个下载地址的尝试次数（断点续传）
_RETRY_WAIT = 2


class UpdateError(Exception):
    """检查或下载更新失败（给用户看的友好信息）。"""


class _Abort(UpdateError):
    """不可重试的下载失败（取消、超时、大小异常、校验失败），会删掉半截文件。"""


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
    """有新版本时返回 {version, notes, url, urls, name, size, sha256, page}，否则 None。

    先查国内镜像，镜像不可用再查 GitHub；都失败抛 UpdateError。
    urls 是按优先级排列的下载地址（镜像在前，GitHub 兜底）。
    """
    try:
        return _check_mirror()
    except UpdateError:
        return _check_github()


def _check_mirror() -> dict | None:
    import requests  # 延迟导入：不拖慢窗口首次显示

    try:
        resp = requests.get(MIRROR_LATEST, timeout=_MIRROR_TIMEOUT, headers={"User-Agent": _UA})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise UpdateError(f"镜像不可用：{type(e).__name__}")
    if not isinstance(data, dict):
        raise UpdateError("镜像返回内容无法解析")
    version = str(data.get("version") or "")
    name = Path(str(data.get("name") or "")).name
    if not parse_version(version) or not name.lower().endswith(".exe"):
        raise UpdateError("镜像返回内容无法解析")
    if not is_newer(version):
        return None
    urls = [MIRROR_BASE + name]
    github_url = str(data.get("github_url") or "")
    if github_url.startswith("https://github.com/"):
        urls.append(github_url)
    return {
        "version": version.lstrip("vV"),
        "notes": str(data.get("notes") or "").strip(),
        "url": urls[0],
        "urls": urls,
        "name": name,
        "size": int(data.get("size") or 0),
        "sha256": str(data.get("sha256") or "").lower(),
        "page": str(data.get("page") or "") or RELEASES_PAGE,
    }


def _check_github() -> dict | None:
    import requests

    try:
        resp = requests.get(LATEST_API, timeout=_TIMEOUT, headers={
            "User-Agent": _UA, "Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        raise UpdateError(f"无法连接更新服务器：{type(e).__name__}")
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
    name = Path(str(asset.get("name") or "")).name
    url = asset.get("browser_download_url")
    return {
        "version": version.lstrip("vV"),
        "notes": str(data.get("body") or "").strip(),
        "url": url,
        # 镜像的 latest.json 读不到时，同名安装包可能还在镜像上：先试镜像
        "urls": [MIRROR_BASE + name, url],
        "name": name,
        "size": int(asset.get("size") or 0),
        "sha256": digest[7:].lower() if digest.startswith("sha256:") else "",
        "page": data.get("html_url") or RELEASES_PAGE,
    }


def download(info: dict, progress=None, cancelled=None) -> str:
    """下载安装包到临时目录并校验 sha256，返回本地路径。

    依次尝试 info["urls"]（没有则用 info["url"]），网络失败时断点续传重试几次再换下一个地址。
    网络失败时保留 .part，下次再点升级接着下；取消、校验失败等则删掉。
    progress(done_bytes, total_bytes) 用于报告进度；cancelled() 返回 True 时中止。
    """
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
    if total and part.exists() and part.stat().st_size > total:
        part.unlink()
    urls = [u for u in (info.get("urls") or [info.get("url")]) if u]
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE
    last_error = "下载失败"
    try:
        for url in urls:
            for attempt in range(_RETRIES):
                if attempt:
                    time.sleep(_RETRY_WAIT)
                try:
                    size = _fetch(url, part, total, progress, cancelled, deadline)
                except _Abort:
                    raise
                except UpdateError as e:
                    last_error = str(e)
                    if "HTTP 4" in last_error:
                        break   # 这个地址没有该文件：换下一个
                    continue
                return _finish(part, target, size, info.get("sha256"))
    except _Abort:
        part.unlink(missing_ok=True)
        raise
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    raise UpdateError(last_error)


def _fetch(url, part: Path, total, progress, cancelled, deadline) -> int:
    """从 url 续传到 part，返回总大小。网络问题抛 UpdateError（可重试），其余抛 _Abort。"""
    import requests

    done = part.stat().st_size if part.exists() else 0
    if total and done == total:
        return total
    headers = {"User-Agent": _UA}
    if done:
        headers["Range"] = f"bytes={done}-"
    try:
        with requests.get(url, stream=True, timeout=_TIMEOUT, headers=headers) as resp:
            if resp.status_code == 416 and done:
                part.unlink(missing_ok=True)   # 续传位置无效：重试时从头下
                raise UpdateError("下载失败（续传位置无效）")
            if resp.status_code >= 400:
                raise UpdateError(f"下载失败（HTTP {resp.status_code}）")
            if done and resp.status_code != 206:
                done = 0                       # 服务器不支持续传：从头下
            length = int(resp.headers.get("Content-Length") or 0)
            total = total or (done + length if length else 0)
            with open(part, "ab" if done else "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if cancelled is not None and cancelled():
                        raise _Abort("已取消下载")
                    if time.monotonic() > deadline:
                        raise _Abort("下载超时")
                    done += len(chunk)
                    if done > _MAX_BYTES or (total and done > total):
                        raise _Abort("安装包大小异常，已放弃下载")
                    f.write(chunk)
                    if progress is not None:
                        progress(done, total)
    except requests.RequestException as e:
        raise UpdateError(f"下载失败：{type(e).__name__}")
    if total and done != total:
        raise UpdateError("下载不完整，请重试")
    return total or done


def _finish(part: Path, target: Path, total, expected) -> str:
    if total and part.stat().st_size != total:
        raise _Abort("下载不完整，请重试")
    if expected:
        sha = hashlib.sha256()
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        if sha.hexdigest() != expected:
            raise _Abort("安装包校验失败（sha256 不一致），请重试")
    os.replace(part, target)
    return str(target)


def launch_installer(path: str):
    """静默安装并在完成后重启程序；调用方随后应正常退出，让安装程序替换文件。"""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH"],
                     creationflags=flags, close_fds=True)
