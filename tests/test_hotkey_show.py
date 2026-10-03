# -*- coding: utf-8 -*-
"""呼出主界面的全局快捷键：默认值、解析、旧配置升级后自动补上。"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_show_hotkey_default_and_parse():
    from pda import config, hotkey

    assert config.HOTKEY_ACTIONS["show"][0] == "Ctrl+Alt+Space"
    mods, vk, err = hotkey.parse("Ctrl+Alt+Space")
    assert err == ""
    assert mods == hotkey.MOD_CONTROL | hotkey.MOD_ALT
    assert vk == 0x20


def test_old_config_without_show_gets_default():
    from pda import config

    config.CONFIG_PATH.write_text(
        json.dumps({"hotkeys": {"clipboard": "Ctrl+Shift+Q", "selection": ""}}), encoding="utf-8")
    try:
        hk = config.get_hotkeys()
        assert hk["show"] == "Ctrl+Alt+Space"
        assert hk["selection"] == ""  # 用户关掉的保持关闭
    finally:
        config.CONFIG_PATH.unlink()
