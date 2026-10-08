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


def test_update_ui_entry_and_toast(monkeypatch):
    """手动检查"已是最新"必须是结果态 toast（会自动消失）；发现新版显示侧栏入口而不是自动弹窗。"""
    import os
    import types

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from pda import config
    from pda.ui import main_window as mw

    app = QApplication.instance() or QApplication([])
    win = types.SimpleNamespace(
        _update_checking=True, _update_download=None, _update_toast=None, _update_info=None,
        _shutting_down=False, tray=None, update_btn=QPushButton(),
        _progress_toast=mw.MainWindow._progress_toast)
    for name in ("_set_update_available", "_on_update_checked"):
        setattr(win, name, types.MethodType(getattr(mw.MainWindow, name), win))
    prompted = []
    win._prompt_update = prompted.append

    win._on_update_checked({"ok": True, "info": None}, manual=True)
    assert win._update_toast is not None and not win._update_toast._pending
    win._update_toast.close()

    info = {"version": "9.9.9", "notes": "", "size": 0}
    monkeypatch.setattr(config, "skipped_version", lambda: "")
    win._on_update_checked({"ok": True, "info": info}, manual=False)
    assert win.update_btn.isVisibleTo(None) or not win.update_btn.isHidden()
    assert "9.9.9" in win.update_btn.text() and prompted == []   # 自动检查不弹窗

    win._on_update_checked({"ok": True, "info": info}, manual=True)
    assert prompted == [info]                                       # 手动检查直接弹说明

    monkeypatch.setattr(config, "skipped_version", lambda: "9.9.9")
    win._set_update_available(None)
    win._on_update_checked({"ok": True, "info": info}, manual=False)
    assert win.update_btn.isHidden()                                # 跳过的版本不再提示
    app.processEvents()
