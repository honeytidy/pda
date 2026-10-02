# -*- coding: utf-8 -*-
"""设置对话框：填 Key 后直接点"保存"，Key 必须被保存（回归：模型列表查询中保存被静默拒绝）。"""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_save_while_model_list_fetching(app, monkeypatch):
    from PySide6.QtCore import QTimer

    from pda import config, llm
    from pda.ui import main_window as mw

    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()

    def slow_list(*a, **k):
        time.sleep(1.0)
        return ["kimi-k2-0905-preview"]

    monkeypatch.setattr(llm, "list_chat_models", slow_list)
    monkeypatch.setattr(llm, "verify_and_pick_model",
                        lambda key, base, prefs, model="", timeout=12.0: model or prefs[0])

    dlg = mw.WatchFoldersDialog(mw.config.get_hotkeys())
    dlg.api_key_edit.setText("sk-testkey-123")
    dlg.api_key_edit.editingFinished.emit()  # 点"保存"时输入框失焦，触发自动查询模型
    assert dlg._models_worker is not None
    QTimer.singleShot(0, dlg.accept)
    QTimer.singleShot(5000, dlg.reject)  # 兜底：保存没生效时不让测试卡住
    assert dlg.exec() == 1

    values = dlg.llm_values()
    assert values["api_key"] == "sk-testkey-123"
    assert values["model"] == "kimi-k2-0905-preview"  # "自动选择" 由验证选定模型
    config.save_llm_config(**values)
    try:
        assert llm.has_llm()
    finally:
        config.CONFIG_PATH.unlink()
