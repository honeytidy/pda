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


class _StreamClient:
    """记录每次请求的参数；reject 里的参数名首次出现时按服务商的方式返回 400。"""

    def __init__(self, reject=()):
        self.reject, self.calls = set(reject), []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import httpx
        import openai

        self.calls.append(kw)
        if "thinking" in self.reject and "extra_body" in kw:
            req = httpx.Request("POST", "https://x/v1/chat/completions")
            raise openai.BadRequestError("unknown field: thinking",
                                         response=httpx.Response(400, request=req), body=None)
        if kw.get("stream"):
            def gen():
                for piece in ("你", "好", None):
                    delta = types.SimpleNamespace(content=piece)
                    yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])
                yield types.SimpleNamespace(choices=[])  # usage 块：没有 choices
            return gen()
        msg = types.SimpleNamespace(content="完整回答")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


_KIMI = {"api_key": "sk-x", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k3",
         "model_auto": False}


def test_chat_disables_thinking_and_streams(monkeypatch):
    from pda import llm

    client = _StreamClient()
    monkeypatch.setattr(llm, "_get_client", lambda cfg: client)
    llm._THINKING_REJECTED.clear()
    deltas = []
    text = llm._chat_with(_KIMI, "kimi-k3", [{"role": "user", "content": "hi"}], 0.3, 60,
                          on_delta=deltas.append)
    assert text == "你好" and deltas == ["你", "你好"]
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.calls[0]["stream"] is True

    # 不认识的服务商：不带思考参数
    other = dict(_KIMI, base_url="https://example.com/v1")
    assert llm._chat_with(other, "m", [], 0.3, 60) == "完整回答"
    assert "extra_body" not in client.calls[-1]


def test_chat_retries_without_thinking_when_rejected(monkeypatch):
    from pda import llm

    client = _StreamClient(reject={"thinking"})
    monkeypatch.setattr(llm, "_get_client", lambda cfg: client)
    llm._THINKING_REJECTED.clear()
    assert llm._chat_with(_KIMI, "old-model", [], 0.3, 60) == "完整回答"
    assert "extra_body" in client.calls[0] and "extra_body" not in client.calls[1]
    # 记住被拒：同一模型之后不再带
    llm._chat_with(_KIMI, "old-model", [], 0.3, 60)
    assert "extra_body" not in client.calls[2]
    llm._THINKING_REJECTED.clear()


def test_answer_streams_with_scope_prefix(monkeypatch):
    from pda import llm, qa

    hit = {"chunk_id": "c1", "title": "会议纪要", "file_path": "D:/a.md", "text": "发布会十月二十号"}
    monkeypatch.setattr(qa, "retrieve", lambda *a, **k: [hit])
    monkeypatch.setattr(qa.embeddings, "embed", lambda texts: [[0.0]])
    monkeypatch.setattr(qa.db, "find_documents_by_term", lambda term: [])
    monkeypatch.setattr(llm, "has_llm", lambda: True)

    def fake_chat(messages, on_delta=None, **kw):
        on_delta("十月")
        on_delta("十月二十号")
        return "十月二十号"

    monkeypatch.setattr(llm, "chat", fake_chat)
    seen = []
    result = qa.answer("在会议纪要里发布会是哪天", on_delta=seen.append)
    assert result["markdown"] and result["answer"].endswith("十月二十号")
    assert seen[-1] == result["answer"]          # 流式文本与最终答案一致（含范围提示前缀）
    assert seen[0].startswith("（未找到匹配")
