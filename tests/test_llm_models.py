# -*- coding: utf-8 -*-
"""模型"自动"：选账户下最新的通用对话模型；旧配置迁移；模型下线时重选。"""
import json
import types

import pytest


def test_pick_latest_prefers_newest_general_model():
    from pda import llm

    models = [
        ("moonshot-v1-8k", 1_700_000_000),
        ("kimi-k2.6", 1_760_000_000),
        ("kimi-k3", 1_780_000_000),
        ("kimi-k2.7-code", 1_790_000_000),       # 更新，但是代码专用
        ("kimi-k3-vision-preview", 1_795_000_000),
        ("text-embedding-x", 1_799_000_000),     # 不是对话模型
    ]
    assert llm.pick_latest_model(models) == "kimi-k3"


def test_pick_latest_without_created_uses_version_in_name():
    from pda import llm

    assert llm.pick_latest_model([("kimi-k2.6", 0), ("kimi-k3", 0)]) == "kimi-k3"
    assert llm.pick_latest_model([("glm-4", 0), ("glm-5", 0)]) == "glm-5"
    assert llm.pick_latest_model([]) == ""
    # 只有专用模型时也要能选出一个
    assert llm.pick_latest_model([("kimi-k2.7-code", 0)]) == "kimi-k2.7-code"


def _write_cfg(data):
    from pda import config

    config.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def cfg_cleanup():
    from pda import config, llm

    llm._auto_models.clear()
    yield
    llm._auto_models.clear()
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()


def test_old_config_without_flag_is_auto(cfg_cleanup):
    from pda import config

    # 老版本"自动选择"存下的就是当时挑中的名字（已下线），没有 model_auto 键
    _write_cfg({"api_key": "sk-x", "model": "kimi-k2-0905-preview"})
    assert config.get_llm_config()["model_auto"] is True
    _write_cfg({"api_key": "sk-x", "model": "kimi-k2.6", "model_auto": False})
    assert config.get_llm_config()["model_auto"] is False


class _FakeClient:
    def __init__(self, ids, gone=()):
        self.ids, self.gone, self.calls = ids, set(gone), []
        self.models = types.SimpleNamespace(list=lambda: [
            types.SimpleNamespace(id=i, created=n) for n, i in enumerate(self.ids)])
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def with_options(self, **kw):
        return self

    def _create(self, model, **kw):
        import httpx
        import openai

        self.calls.append(model)
        if model in self.gone:
            req = httpx.Request("POST", "https://x/v1/chat/completions")
            raise openai.NotFoundError("model not found", response=httpx.Response(404, request=req),
                                       body=None)
        msg = types.SimpleNamespace(content=f"answer from {model}")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def test_chat_auto_uses_latest_and_records_it(cfg_cleanup, monkeypatch):
    from pda import config, llm

    _write_cfg({"api_key": "sk-x", "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2-0905-preview"})
    client = _FakeClient(["kimi-k2.6", "kimi-k3"])
    monkeypatch.setattr(llm, "_get_client", lambda cfg: client)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "answer from kimi-k3"
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["model"] == "kimi-k3" and saved["model_auto"] is True


def test_chat_fixed_model_not_overridden(cfg_cleanup, monkeypatch):
    from pda import llm

    _write_cfg({"api_key": "sk-x", "model": "kimi-k2.6", "model_auto": False})
    client = _FakeClient(["kimi-k2.6", "kimi-k3"])
    monkeypatch.setattr(llm, "_get_client", lambda cfg: client)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "answer from kimi-k2.6"


def test_chat_auto_reselects_when_model_gone(cfg_cleanup, monkeypatch):
    from pda import llm

    _write_cfg({"api_key": "sk-x", "model": "kimi-k3", "model_auto": True})
    client = _FakeClient(["kimi-k3"], gone={"kimi-k3"})
    monkeypatch.setattr(llm, "_get_client", lambda cfg: client)
    # 本进程先解析出 kimi-k3；之后服务商下线它、上了 kimi-k4
    llm._auto_models[("sk-x", "https://api.moonshot.cn/v1")] = "kimi-k3"
    client.ids = ["kimi-k4"]
    assert llm.chat([{"role": "user", "content": "hi"}]) == "answer from kimi-k4"
    assert client.calls == ["kimi-k3", "kimi-k4"]


def test_verify_auto_picks_latest(monkeypatch):
    from pda import llm

    client = _FakeClient(["moonshot-v1-8k", "kimi-k2.6", "kimi-k3"])
    monkeypatch.setattr(llm, "_openai_client", lambda **kw: client)
    assert llm.verify_and_pick_model("sk-x", "https://api.moonshot.cn/v1", ["kimi-k2.6"]) == "kimi-k3"
    assert llm.verify_and_pick_model("sk-x", "https://api.moonshot.cn/v1", [], "kimi-k2.6") == "kimi-k2.6"
    with pytest.raises(llm.LlmSetupError):
        llm.verify_and_pick_model("sk-x", "https://api.moonshot.cn/v1", [], "kimi-k2-0905-preview")
