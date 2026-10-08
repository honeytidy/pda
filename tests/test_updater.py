# -*- coding: utf-8 -*-
import hashlib

import pytest


def test_version_compare():
    from pda import updater

    assert updater.parse_version("v0.2.10") == (0, 2, 10)
    assert updater.parse_version("garbage") == ()
    assert updater.is_newer("v0.2.0", "0.1.9")
    assert updater.is_newer("0.1.10", "0.1.9")      # 按数字比较，不按字符串
    assert not updater.is_newer("v0.1", "0.1.0")     # 补零后相等
    assert not updater.is_newer("v0.1.0", "0.2.0")
    assert not updater.is_newer("", "0.1.0")


class _Resp:
    def __init__(self, status=200, data=None, body=b"", headers=None):
        self.status_code = status
        self._data, self._body = data, body
        self.headers = headers or {}

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), 4):
            yield self._body[i:i + 4]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _release(tag, assets):
    return {"tag_name": tag, "body": "修复若干问题", "html_url": "https://x/r",
            "draft": False, "prerelease": False, "assets": assets}


def test_check_latest(monkeypatch):
    import requests
    from pda import updater

    asset = {"name": "pda-v9.0.0.exe", "size": 3, "digest": "sha256:ABC",
             "browser_download_url": "https://x/a.exe"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(data=_release("v9.0.0", [asset])))
    info = updater.check_latest()
    assert info["version"] == "9.0.0" and info["url"] == "https://x/a.exe"
    assert info["sha256"] == "abc" and info["notes"] == "修复若干问题"

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(data=_release("v0.0.1", [asset])))
    assert updater.check_latest() is None   # 不比当前新

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(data=_release("v9.0.0", [])))
    with pytest.raises(updater.UpdateError, match="安装包"):
        updater.check_latest()

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status=404))
    assert updater.check_latest() is None   # 还没有发布


def test_download_verifies_sha256(monkeypatch, tmp_path):
    import requests
    from pda import updater

    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    body = b"installer-bytes"
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(body=body))
    info = {"url": "https://x/a.exe", "name": "../evil.exe", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(), "version": "9.0.0"}
    seen = []
    path = updater.download(info, progress=lambda d, t: seen.append((d, t)))
    assert open(path, "rb").read() == body
    assert path.endswith("evil.exe") and str(tmp_path) in path   # 资源名不能跳出临时目录
    assert seen[-1] == (len(body), len(body))

    info["sha256"] = "0" * 64
    with pytest.raises(updater.UpdateError, match="校验"):
        updater.download(info)
    assert not list((tmp_path / "pda_update").glob("*.part"))   # 失败不留半截文件

    info["sha256"] = ""
    with pytest.raises(updater.UpdateError, match="取消"):
        updater.download(info, cancelled=lambda: True)
