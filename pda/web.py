# -*- coding: utf-8 -*-
"""网页链接抓取收录：抓取正文 → 存为 markdown 笔记 → 走正常入库管道。

网络请求必须在 worker 线程调用（本模块不做线程管理）。
"""
import ipaddress
import re
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import config

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TIMEOUT = 15          # 单次连接/读取超时
_TOTAL_DEADLINE = 30   # 整次抓取总时长上限（慢速滴流的服务器单次读取永远不超时）
_MAX_BYTES = 10 * 1024 * 1024  # 网页正文抓取上限，防止链接指向大文件时整个读进内存
_MAX_REDIRECTS = 5
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")

URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


class FetchError(Exception):
    """抓取或正文提取失败（给用户看的友好信息）。"""


def is_bare_url(text: str) -> bool:
    """整条输入就是一个裸 URL 时返回 True。"""
    return bool(URL_RE.match(text.strip()))


def _check_public_url(url: str):
    """只允许抓取公网 http(s) 地址：拒绝本机 / 内网 / 链路本地等目标（含重定向后的目标），
    避免一个网页通过重定向让本程序去访问本机服务或局域网设备。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchError("只支持 http/https 网页链接")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        raise FetchError(f"无法解析域名：{host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if ip.version == 4 and ip in _FAKE_IP_NET:
            continue  # Clash 等代理的 fake-ip 模式：所有域名都解析到这里，实际经代理出网
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FetchError(f"不抓取本机或内网地址：{host}")


def _download(url: str) -> tuple:
    """手动跟随重定向（每一跳都做地址检查），返回 (response, raw_bytes)。"""
    import requests  # 延迟导入：不拖慢窗口首次显示

    deadline = time.monotonic() + _TOTAL_DEADLINE
    for _ in range(_MAX_REDIRECTS + 1):
        _check_public_url(url)
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT,
                            stream=True, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise FetchError(f"抓取失败（HTTP {resp.status_code} 无跳转地址）")
            url = urljoin(url, location)
            continue
        if resp.status_code >= 400:
            resp.close()  # stream=True：不关闭的话连接一直占着
            resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            resp.close()
            raise FetchError(f"链接不是网页（{ctype.split(';')[0]}），请下载后拖入收录")
        chunks, total = [], 0
        try:
            for chunk in resp.iter_content(65536):
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise FetchError("页面过大（超过 10MB），已放弃抓取")
                if time.monotonic() > deadline:
                    raise FetchError(f"抓取超时（{_TOTAL_DEADLINE} 秒）")
                chunks.append(chunk)
        finally:
            resp.close()
        return resp, b"".join(chunks)
    raise FetchError("重定向次数过多")


def fetch_webpage(url: str) -> tuple:
    """抓取 URL，返回 (title, markdown_text)。失败抛 FetchError。"""
    import requests

    try:
        resp, raw = _download(url)
    except requests.Timeout:
        raise FetchError(f"抓取超时（{_TIMEOUT} 秒）")
    except requests.HTTPError as e:
        raise FetchError(f"抓取失败（HTTP {e.response.status_code}）")
    except requests.RequestException as e:
        raise FetchError(f"网络错误：{type(e).__name__}")

    try:
        from readability import Document
    except ImportError as e:
        raise FetchError(
            f"网页抓取组件未安装（readability-lxml / lxml_html_clean）：{e}。"
            "请执行 pip install -r requirements.txt"
        )

    try:
        # 服务器没带 charset 时 requests 按 ISO-8859-1 解码会乱码：把字节交给
        # readability/lxml，由它按 <meta charset> 判断；声明了 charset 才用解码后的文本
        declared = "charset=" in resp.headers.get("Content-Type", "").lower()
        source = raw
        if declared:
            try:
                source = raw.decode(resp.encoding or "utf-8", errors="replace")
            except LookupError:
                source = raw  # 服务器声明了不存在的编码名：交给 lxml 自行判断
        doc = Document(source)
        title = (doc.short_title() or "").strip() or url
        html = doc.summary()
    except Exception as e:
        raise FetchError(f"正文提取失败：{e}")

    text = _html_to_text(html).strip()
    if not text:
        raise FetchError("页面没有可提取的正文")
    return title, f"# {title}\n\n来源：{url}\n\n{text}\n"


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def save_webpage_note(title: str, markdown: str) -> str:
    """存到 data/notes/网页_标题_时间戳.md，返回路径。"""
    config.ensure_dirs()
    # 去掉非法字符与控制字符（换行/制表符等），Windows 保留名（CON/NUL…）加前缀
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title)[:40].strip(" .") or "未命名"
    if safe.upper().split(".")[0] in _RESERVED:
        safe = "_" + safe
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = Path(config.NOTES_DIR) / f"网页_{safe}_{stamp}.md"
    n = 1
    while path.exists():
        path = Path(config.NOTES_DIR) / f"网页_{safe}_{stamp}_{n}.md"
        n += 1
    path.write_text(markdown, encoding="utf-8")
    return str(path)


_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre",
               "tr", "dt", "dd", "figcaption"}


def _html_to_text(html: str) -> str:
    """readability 的 summary HTML 转纯文本（标题/段落/列表/表格行各成一段）。

    只取"最内层"的块元素：<li><p>x</p></li> 不会把 x 输出两次；直接写在 <div> 里、
    不在任何块元素中的文字也保留。
    """
    import lxml.html

    try:
        root = lxml.html.fromstring(html)
        parts = []

        def walk(el):
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag in ("script", "style"):
                return
            if tag in _BLOCK_TAGS and not any(
                isinstance(d.tag, str) and d.tag in _BLOCK_TAGS for d in el.iterdescendants()
            ):
                if tag == "tr":  # 表格行：单元格之间留分隔，不粘成一串
                    t = " | ".join(
                        "".join(c.itertext()).strip() for c in el if c.tag in ("td", "th")
                    ).strip(" |")
                else:
                    t = "".join(el.itertext()).strip()  # 保留 <pre> / <br> 的换行
                if t:
                    parts.append(t)
                return
            # 容器元素：自身的散落文字（text 与各子元素的 tail）按原顺序单独成段
            loose = [el.text or ""]

            def flush():
                t = " ".join("".join(loose).split())
                if t:
                    parts.append(t)
                loose.clear()

            for child in el:
                flush()
                walk(child)
                loose.append(child.tail or "")
            flush()

        walk(root)
        return "\n\n".join(parts)
    except Exception:
        # 兜底：粗暴去标签
        return re.sub(r"<[^>]+>", "\n", html)
