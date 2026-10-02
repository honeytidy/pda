# -*- coding: utf-8 -*-
"""可插拔 LLM：OpenAI 兼容客户端。无 key 时 has_llm() = False。"""
import logging
import re
import threading
import time

from . import config

_log = logging.getLogger(__name__)


def _openai_client(*args, **kwargs):
    """延迟导入 openai（导入要几百毫秒，不拖慢窗口首次显示）。"""
    from openai import OpenAI as _OpenAI

    return _OpenAI(*args, **kwargs)

_client = None
_client_key = None  # (api_key, base_url)：配置变了就重建客户端，不必重启
_client_lock = threading.Lock()  # 问答线程与入库标签线程可能同时取客户端

# 默认 600 秒超时 + 2 次重试：断网时一次入库会卡几十分钟
_CHAT_TIMEOUT = 60.0
_TAG_TIMEOUT = 20.0

# 自动标签熔断：连不上服务商后这段时间内不再请求，批量入库不必每个文件都等一次超时
_TAG_OFFLINE_BACKOFF = 300.0
_tags_offline_until = 0.0


def has_llm() -> bool:
    return bool(config.get_llm_config()["api_key"])


# ---------- 服务商预设：用户只填 Key，接口地址与模型自动确定 ----------
# models 是偏好顺序：验证时从该服务商 /models 返回的列表里挑第一个存在的（模型会上下架，
# 不硬编码单一名字）；/models 不可用时用第一个。
# key_url：该服务商创建/查看 API Key 的控制台页面（设置界面"获取 API Key"按钮打开）
PROVIDERS = [
    {"id": "moonshot", "name": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1",
     "key_url": "https://platform.kimi.com/console/api-keys",
     "models": ["kimi-k2-0905-preview", "kimi-k2-turbo-preview", "kimi-latest", "moonshot-v1-32k", "moonshot-v1-8k"]},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "key_url": "https://platform.deepseek.com/api_keys",
     "models": ["deepseek-chat"]},
    {"id": "dashscope", "name": "通义千问（阿里云百炼）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "key_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
     "models": ["qwen-plus", "qwen-turbo", "qwen-max"]},
    {"id": "zhipu", "name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
     "models": ["glm-4-flash", "glm-4-plus", "glm-4"]},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "key_url": "https://cloud.siliconflow.cn/account/ak",
     "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-7B-Instruct"]},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
     "key_url": "https://platform.openai.com/api-keys",
     "models": ["gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"]},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "key_url": "https://openrouter.ai/settings/keys",
     "models": ["deepseek/deepseek-chat", "openai/gpt-4o-mini"]},
    {"id": "anthropic", "name": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1",
     "key_url": "https://console.anthropic.com/settings/keys",
     "models": ["claude-haiku-4-5", "claude-sonnet-5"]},
]
CUSTOM_PROVIDER = "custom"
_PROVIDER_BY_ID = {p["id"]: p for p in PROVIDERS}


def get_provider(provider_id: str):
    return _PROVIDER_BY_ID.get(provider_id)


def provider_for_base_url(base_url: str):
    """已保存的接口地址对应哪个预设服务商（设置界面回显用）；不是预设返回 None。"""
    norm = (base_url or "").strip().rstrip("/").lower()
    for p in PROVIDERS:
        if p["base_url"].rstrip("/").lower() == norm:
            return p["id"]
    return None


def guess_provider(api_key: str):
    """按 Key 的格式猜服务商，只认有独特前缀/格式的几家；猜不出返回 None。

    故意不做"拿 Key 挨个服务商试"：那会把用户的 Key 发给无关的第三方。
    """
    key = (api_key or "").strip()
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith(("sk-proj-", "sk-svcacct-")):
        return "openai"
    if not key.startswith("sk-") and re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9]{10,}", key):
        return "zhipu"  # 智谱 Key 形如 <32位hex>.<secret>
    return None


class LlmSetupError(Exception):
    """验证 Key 失败，消息可直接展示给用户。offline=True 表示是网络问题（Key 本身未必有错）。"""

    def __init__(self, msg: str, offline: bool = False):
        super().__init__(msg)
        self.offline = offline


# /models 里混着向量、语音、绘图等模型，下拉框只列能对话的
_NON_CHAT_MARKERS = ("embed", "tts", "whisper", "dall-e", "audio", "speech", "transcri",
                     "moderation", "rerank", "bge-", "image", "cogview", "cogvideo", "wanx", "sora")


def list_chat_models(api_key: str, base_url: str, prefs: list = (), timeout: float = 12.0) -> list:
    """取该服务商账户下可用的对话模型（设置界面下拉框用）。偏好模型排前面，其余按名字排序。

    该服务商不支持 /models 时返回 []（界面退回预设列表 + 手动输入）。失败抛 LlmSetupError。
    """
    import openai

    client = _openai_client(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)
    try:
        ids = {m.id for m in client.models.list()}
    except (openai.NotFoundError, openai.BadRequestError):
        return []
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        raise LlmSetupError("API Key 无效，或与所选服务商不匹配") from e
    except (openai.APITimeoutError, openai.APIConnectionError) as e:
        raise LlmSetupError("连不上服务商，无法获取模型列表", offline=True) from e
    except Exception as e:
        raise LlmSetupError(f"获取模型列表失败：{e}") from e
    chat_ids = [i for i in ids if not any(m in i.lower() for m in _NON_CHAT_MARKERS)]
    head = [p for p in prefs if p in chat_ids]
    return head + sorted(i for i in chat_ids if i not in head)


def verify_and_pick_model(api_key: str, base_url: str, model_prefs: list,
                          model_override: str = "", timeout: float = 12.0) -> str:
    """用 Key 调一次该服务商接口，确认可用并返回要使用的模型名。失败抛 LlmSetupError。

    只向用户选定的这一家发请求。优先调 /models（不消耗额度），拿到列表后按偏好挑模型；
    该服务商不支持 /models 时，用偏好模型发一条 1 token 的对话来验证。
    """
    import openai

    client = _openai_client(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)
    prefs = [m for m in ([model_override] if model_override else []) + list(model_prefs) if m]

    def _explain(e) -> LlmSetupError:
        if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LlmSetupError("API Key 无效，或与所选服务商不匹配")
        if isinstance(e, openai.RateLimitError):
            return LlmSetupError("请求被限流或账户余额不足，请到服务商控制台确认")
        if isinstance(e, openai.APITimeoutError):
            return LlmSetupError("连接超时，请检查网络", offline=True)
        if isinstance(e, openai.APIConnectionError):
            return LlmSetupError("连不上接口地址，请检查网络或接口地址", offline=True)
        if isinstance(e, openai.APIStatusError):
            # 502/503/504 多半是代理或网关连不上上游，属于网络问题，不代表 Key 有错
            offline = e.status_code in (502, 503, 504)
            return LlmSetupError(f"服务商返回错误（HTTP {e.status_code}）", offline=offline)
        return LlmSetupError(f"验证失败：{e}")

    ids = None
    try:
        ids = [m.id for m in client.models.list()]
    except (openai.NotFoundError, openai.BadRequestError):
        ids = None  # 该服务商没有 /models：改用对话验证
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        # 有的兼容层 /models 用另一套鉴权（如只认 x-api-key），对话接口却认 Bearer：
        # 用对话再确认一次，两边都拒绝才判定 Key 无效
        if not prefs:
            raise _explain(e) from e
        ids = None
    except Exception as e:
        raise _explain(e) from e

    if ids:
        if model_override:
            if model_override not in ids:
                raise LlmSetupError(f"该账户下没有模型 {model_override}")
            return model_override
        for pref in prefs:
            if pref in ids:
                return pref
        for pref in prefs:  # 带日期后缀的版本名，例如 claude-haiku-4-5-20251001
            hit = next((i for i in ids if i.startswith(pref)), None)
            if hit:
                return hit
        if not prefs:
            return ids[0]  # 自定义接口且没指定模型：用列表第一个

    if not prefs:
        raise LlmSetupError("无法获取模型列表，请在「模型」里填写模型名")
    try:
        client.chat.completions.create(
            model=prefs[0], messages=[{"role": "user", "content": "hi"}], max_tokens=1
        )
    except Exception as e:
        err = _explain(e)
        if isinstance(e, (openai.NotFoundError, openai.BadRequestError)):
            err = LlmSetupError(f"模型 {prefs[0]} 不可用，请在「模型」里换一个")
        raise err from e
    return prefs[0]


def _get_client(cfg: dict):
    global _client, _client_key
    key = (cfg["api_key"], cfg["base_url"])
    with _client_lock:
        if _client is None or _client_key != key:
            _client = _openai_client(api_key=cfg["api_key"], base_url=cfg["base_url"], max_retries=1)
            _client_key = key
        return _client


# 不接受自定义 temperature 的模型（推理模型如 kimi-k2.5 / o 系列只允许默认值 1）：
# 首次被拒后记住，之后不再传该参数
_NO_TEMPERATURE_MODELS = set()


def _is_temperature_rejected(e) -> bool:
    import openai

    return isinstance(e, openai.BadRequestError) and "temperature" in str(e).lower()


def chat(messages: list, temperature: float = 0.3, timeout: float = _CHAT_TIMEOUT) -> str:
    cfg = config.get_llm_config()
    model = cfg["model"]
    kwargs = dict(model=model, messages=messages, timeout=timeout)
    client = _get_client(cfg)
    if model not in _NO_TEMPERATURE_MODELS:
        try:
            resp = client.chat.completions.create(temperature=temperature, **kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            if not _is_temperature_rejected(e):
                raise
            _NO_TEMPERATURE_MODELS.add(model)
    resp = client.chat.completions.create(**kwargs)  # 用服务商默认 temperature
    return resp.choices[0].message.content or ""


def generate_tags(text: str) -> list:
    """对文档内容生成 3-5 个中文标签。失败返回 []（标签是增强功能，不影响入库）。

    会把文档前 1500 字发给配置的 LLM；pda_config.json 里 "auto_tags": false 可关闭。
    """
    global _tags_offline_until
    if not has_llm() or not config.auto_tags_enabled():
        return []
    if time.monotonic() < _tags_offline_until:
        return []
    try:
        resp = chat(
            [
                {
                    "role": "user",
                    "content": (
                        "为以下内容生成 3-5 个中文标签（概括主题/类型/领域），"
                        "只输出逗号分隔的标签，不要输出任何其他内容：\n\n"
                        + text[:1500]
                    ),
                }
            ],
            temperature=0.2,
            timeout=_TAG_TIMEOUT,
        )
        tags = [t.strip().strip("。；;#") for t in re.split(r"[,，、]", resp)]
        return [t for t in tags if t and len(t) <= 12][:5]
    except Exception as e:
        import openai

        if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
            _tags_offline_until = time.monotonic() + _TAG_OFFLINE_BACKOFF
            _log.warning("生成标签时连不上服务商，%d 秒内暂停自动标签", _TAG_OFFLINE_BACKOFF)
        else:
            _log.warning("生成标签失败", exc_info=True)
        return []
