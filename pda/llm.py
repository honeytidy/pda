# -*- coding: utf-8 -*-
"""可插拔 LLM：OpenAI 兼容客户端。无 key 时 has_llm() = False。"""
import logging
import os
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
# 模型"自动"：从该服务商 /models 返回的账户可用列表里选最新的通用对话模型（按发布时间，
# 见 pick_latest_model），模型上下架不用改代码。models 只在该服务商不提供 /models 时兜底，
# 按顺序用第一个。
# key_url：该服务商创建/查看 API Key 的控制台页面（设置界面"获取 API Key"按钮打开）
PROVIDERS = [
    {"id": "moonshot", "name": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1",
     "key_url": "https://platform.kimi.com/console/api-keys",
     # kimi-k2 系列 2026-05-25 下线，moonshot-v1 / kimi-k2.5 2026-08-31 下线
     "models": ["kimi-k3", "kimi-k2.6"]},
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


# 能对话但不适合做通用问答的专用变体："自动"选最新模型时跳过（下拉框里仍可手动选）
_SPECIALIZED_MARKERS = ("code", "coder", "vision", "-vl", "ocr", "math", "search", "realtime",
                        "preview", "highspeed", "thinking", "reason")


def _version_key(model_id: str) -> tuple:
    """模型名里的数字当版本号比较（kimi-k3 > kimi-k2.6、glm-4-plus > glm-4）。"""
    return tuple(int(n) for n in re.findall(r"\d+", model_id))


def _newest_first(models: list) -> list:
    return sorted(models, key=lambda ic: (ic[1] or 0, _version_key(ic[0]), ic[0]), reverse=True)


def pick_latest_model(models: list) -> str:
    """models: [(id, created)]。选最新的通用对话模型：先按发布时间，再按名字里的版本号。

    没有通用模型时退回在所有对话模型里选。列表为空返回 ""。
    """
    chat = [(i, c) for i, c in models if not any(m in i.lower() for m in _NON_CHAT_MARKERS)]
    general = [(i, c) for i, c in chat if not any(m in i.lower() for m in _SPECIALIZED_MARKERS)]
    pool = general or chat
    return _newest_first(pool)[0][0] if pool else ""


def _list_models(client) -> list:
    """[(id, created)]；服务商不支持 /models 时抛 openai.NotFoundError / BadRequestError。"""
    out = []
    for m in client.models.list():
        created = getattr(m, "created", 0)
        out.append((m.id, created if isinstance(created, (int, float)) else 0))
    return out


def list_chat_models(api_key: str, base_url: str, prefs: list = (), timeout: float = 12.0) -> list:
    """取该服务商账户下可用的对话模型（设置界面下拉框用），最新的排前面。

    该服务商不支持 /models 时返回 []（界面退回预设列表 + 手动输入）。失败抛 LlmSetupError。
    """
    import openai

    client = _openai_client(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)
    try:
        models = _list_models(client)
    except (openai.NotFoundError, openai.BadRequestError):
        return []
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        raise LlmSetupError("API Key 无效，或与所选服务商不匹配") from e
    except (openai.APITimeoutError, openai.APIConnectionError) as e:
        raise LlmSetupError("连不上服务商，无法获取模型列表", offline=True) from e
    except Exception as e:
        raise LlmSetupError(f"获取模型列表失败：{e}") from e
    chat = [(i, c) for i, c in models if not any(m in i.lower() for m in _NON_CHAT_MARKERS)]
    return [i for i, _ in _newest_first(chat)]


def verify_and_pick_model(api_key: str, base_url: str, model_prefs: list,
                          model_override: str = "", timeout: float = 12.0) -> str:
    """用 Key 调一次该服务商接口，确认可用并返回要使用的模型名。失败抛 LlmSetupError。

    只向用户选定的这一家发请求。优先调 /models（不消耗额度）：指定了模型就确认它在列表里，
    没指定（"自动"）就选列表里最新的通用对话模型；该服务商不支持 /models 时，
    用指定模型或预设第一个发一条 1 token 的对话来验证。
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
    models = []
    try:
        models = _list_models(client)
        ids = [i for i, _ in models]
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
        latest = pick_latest_model(models)
        if latest:
            return latest

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


# "自动"模式本进程内解析出的模型：(api_key, base_url) -> 模型名。每次启动后首次调用时
# 按账户当前的 /models 重新选一次；调用时模型已下线也会重选
_auto_models = {}
_auto_lock = threading.Lock()


def _fallback_model(base_url: str) -> str:
    p = get_provider(provider_for_base_url(base_url) or "")
    return p["models"][0] if p else ""


def _resolve_model(cfg: dict, refresh: bool = False) -> str:
    if not cfg.get("model_auto"):
        return cfg["model"] or _fallback_model(cfg["base_url"])
    key = (cfg["api_key"], cfg["base_url"])
    with _auto_lock:
        if not refresh and key in _auto_models:
            return _auto_models[key]
    model = ""
    try:
        model = pick_latest_model(_list_models(_get_client(cfg).with_options(timeout=12.0)))
    except Exception:
        _log.warning("自动选择模型：获取模型列表失败，沿用上次的模型", exc_info=True)
    if model:
        try:
            config.save_auto_model(model)  # 设置界面回显"当前使用的模型"
        except Exception:
            _log.warning("记录自动选择的模型失败", exc_info=True)
    else:
        model = cfg["model"] or _fallback_model(cfg["base_url"])
    with _auto_lock:
        _auto_models[key] = model
    return model


def _is_model_gone(e) -> bool:
    import openai

    if isinstance(e, openai.NotFoundError):
        return True
    text = str(e).lower()
    return isinstance(e, (openai.BadRequestError, openai.PermissionDeniedError)) and "model" in text


def chat(messages: list, temperature: float = 0.3, timeout: float = _CHAT_TIMEOUT,
         on_delta=None) -> str:
    """on_delta(text)：传入时流式输出，每收到新正文回调一次截至目前的完整文本。"""
    cfg = config.get_llm_config()
    model = _resolve_model(cfg)
    try:
        return _chat_with(cfg, model, messages, temperature, timeout, on_delta)
    except Exception as e:
        if not (cfg.get("model_auto") and _is_model_gone(e)):
            raise
        # 自动模式下模型被下线：按账户最新列表重选一次再试（请求一开始就失败，还没有输出）
        newer = _resolve_model(cfg, refresh=True)
        if newer == model:
            raise
        return _chat_with(cfg, newer, messages, temperature, timeout, on_delta)


# 问答 / 打标签只是"读资料作答"，不需要深度推理：关闭思考模式。
# 实测 kimi-k3 同一问答从 6.6~11.7 秒降到 2.3~5.8 秒（默认先输出约 70 token 的思考再作答）。
# 只给已知支持该参数的服务商带；被拒（400 且错误信息提到该参数）时记住并去掉重试
_NO_THINKING_BODY = {
    "moonshot": {"thinking": {"type": "disabled"}},
    "zhipu": {"thinking": {"type": "disabled"}},
    "dashscope": {"enable_thinking": False},
}
_THINKING_REJECTED = set()  # (base_url, model)


def _no_thinking_body(base_url: str, model: str) -> dict | None:
    if (base_url, model) in _THINKING_REJECTED:
        return None
    return _NO_THINKING_BODY.get(provider_for_base_url(base_url) or "")


def _is_thinking_rejected(e) -> bool:
    import openai

    return isinstance(e, openai.BadRequestError) and "thinking" in str(e).lower()


def _complete(client, call: dict, on_delta) -> str:
    """on_delta 为 None：普通请求；否则流式，每收到一段正文就回调截至目前的完整文本。"""
    if on_delta is None:
        resp = client.chat.completions.create(**call)
        return resp.choices[0].message.content or ""
    parts = []
    for chunk in client.chat.completions.create(stream=True, **call):
        if not chunk.choices:
            continue
        piece = chunk.choices[0].delta.content  # 思考内容在 reasoning_content 里，不显示
        if piece:
            parts.append(piece)
            on_delta("".join(parts))
    return "".join(parts)


def _chat_with(cfg: dict, model: str, messages: list, temperature: float, timeout: float,
               on_delta=None) -> str:
    kwargs = dict(model=model, messages=messages, timeout=timeout)
    body = _no_thinking_body(cfg["base_url"], model)
    if body:
        kwargs["extra_body"] = body
    use_temperature = model not in _NO_TEMPERATURE_MODELS
    client = _get_client(cfg)
    while True:
        call = dict(kwargs, temperature=temperature) if use_temperature else kwargs
        try:
            return _complete(client, call, on_delta)
        except Exception as e:
            # 参数被拒在请求一开始就返回 400（流式也一样），此时还没有输出正文，可以安全重试
            if use_temperature and _is_temperature_rejected(e):
                _NO_TEMPERATURE_MODELS.add(model)
                use_temperature = False  # 用服务商默认 temperature
            elif "extra_body" in kwargs and _is_thinking_rejected(e):
                _THINKING_REJECTED.add((cfg["base_url"], model))
                kwargs.pop("extra_body")
            else:
                raise


# ---------- 图片转写（视觉模型） ----------
# OCR 只给出零散文字，表格/日历的行列关系丢了（"校历第几周"答不出）。开启 vision_ingest 后
# 收录图片时把整张图发给视觉模型转写成结构化文字，失败由调用方退回 OCR。
_VISION_TIMEOUT = 180.0  # 流式下是两段输出之间的读超时；kimi-k3 转写一张校历全程约 1 分钟
_VISION_MAX_SIDE = 2048  # 长边超过就缩小：省流量，各家视觉接口也有像素上限
_VISION_MARKERS = ("-vl", "vl-", "vision", "4v", "5v")
_VISION_SKIP = ("ocr", "embed")  # qwen-vl-ocr 之类只出纯文字，不还原结构
# 该服务商没有 /models 或列表里挑不出视觉模型时按顺序试；
# vision_main=True 表示主对话模型本身支持看图，最后用它试
_PROVIDER_VISION = {
    "dashscope": {"models": ["qwen-vl-max", "qwen-vl-plus"]},
    "zhipu": {"models": ["glm-4v-plus", "glm-4v-flash"]},
    "siliconflow": {"models": ["Qwen/Qwen2.5-VL-72B-Instruct"]},
    "moonshot": {"models": [], "vision_main": True},
    "openai": {"models": [], "vision_main": True},
    "openrouter": {"models": [], "vision_main": True},
    "anthropic": {"models": [], "vision_main": True},
}
_VISION_PROMPT = (
    "把这张图片的内容转写成文字，用于个人知识库检索和问答。要求：\n"
    "1. 先用一句话说明这是什么图片（标题/主题）；\n"
    "2. 保留图中有意义的文字、数字、日期，不要评论；\n"
    "3. 日历、校历、课表、排班表等：不要照抄日期网格，改为逐条列出“第N周：起始日期～结束日期”"
    "（写全年月日），再列出图中标注的开学、放假、考试、节假日、调休等安排及其日期；\n"
    "4. 其他表格转成 Markdown 表格，空单元格不要输出成一长串空列。"
)
# 视觉模型偶尔会陷入重复输出（实测 kimi-k3 连续输出几万个空表格单元格）：超长或尾部
# 只剩重复字符时中止，保留之前的有效内容
_VISION_MAX_CHARS = 12000


class _VisionRunaway(Exception):
    def __init__(self, text: str):
        super().__init__("视觉模型输出失控")
        self.text = text


def _vision_guard(text: str):
    tail = text[-600:]
    if len(text) > _VISION_MAX_CHARS or (len(tail) == 600 and len(set(tail) - set(" \n\t")) <= 3):
        raise _VisionRunaway(text)


def _trim_runaway(text: str) -> str:
    """去掉尾部重复的垃圾行（只剩 | - 空格之类的行）。"""
    lines = text.splitlines()
    while lines and len(set(lines[-1]) - set(" |-:\t")) == 0:
        lines.pop()
    return "\n".join(lines).strip()


_VISION_OFFLINE_BACKOFF = 300.0
_vision_offline_until = 0.0


class VisionUnavailable(Exception):
    """没有可用的视觉模型 / 调用失败（调用方退回 OCR）。"""


def _is_vision_model(model_id: str) -> bool:
    low = model_id.lower()
    return any(m in low for m in _VISION_MARKERS) and not any(m in low for m in _VISION_SKIP)


def _vision_candidates(cfg: dict) -> list:
    override = config.vision_model()
    if override:
        return [override]
    preset = _PROVIDER_VISION.get(provider_for_base_url(cfg["base_url"]) or "", {})
    # 主模型本身能看图（kimi-k3 等）：优先用它，列表里的老 vision-preview 模型只作备选
    cands = [_resolve_model(cfg)] if preset.get("vision_main") else []
    try:
        models = _list_models(_get_client(cfg).with_options(timeout=12.0))
        cands += [i for i, _ in _newest_first([m for m in models if _is_vision_model(m[0])])][:2]
    except Exception:
        _log.info("获取模型列表失败，视觉模型用预设", exc_info=True)
    cands += preset.get("models", [])
    if not preset:
        cands.append(_resolve_model(cfg))  # 自定义接口：不知道主模型能不能看图，试一下
    return list(dict.fromkeys(c for c in cands if c))


def _image_data_url(path: str) -> str:
    import base64
    import io

    mime = "image/png"
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.load()
            fmt = (im.format or "").upper()
            if max(im.size) > _VISION_MAX_SIDE or fmt not in ("PNG", "JPEG"):
                im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
                im.thumbnail((_VISION_MAX_SIDE, _VISION_MAX_SIDE))
                buf = io.BytesIO()
                im.save(buf, "PNG")
                data = buf.getvalue()
            else:
                mime = "image/jpeg" if fmt == "JPEG" else "image/png"
                with open(path, "rb") as f:
                    data = f.read()
    except ImportError:
        ext = os.path.splitext(path)[1].lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".bmp": "image/bmp"}.get(ext, "image/png")
        with open(path, "rb") as f:
            data = f.read()
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def describe_image(path: str) -> str:
    """用视觉模型把图片转写成结构化文字。没开启 / 无 Key / 无可用模型 / 失败都抛 VisionUnavailable。"""
    global _vision_offline_until
    import openai

    if not has_llm() or not config.vision_ingest_enabled():
        raise VisionUnavailable("未开启图片 AI 识别")
    if time.monotonic() < _vision_offline_until:
        raise VisionUnavailable("连不上服务商（稍后重试）")
    cfg = config.get_llm_config()
    try:
        url = _image_data_url(path)
    except Exception as e:
        raise VisionUnavailable(f"读取图片失败：{e}") from e
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}},
        {"type": "text", "text": _VISION_PROMPT},
    ]}]
    last = None
    for model in _vision_candidates(cfg):
        try:
            # 流式：整张表转写要一分多钟，非流式请求会在服务端出完全部内容前读超时
            text = _chat_with(cfg, model, messages, 0.1, _VISION_TIMEOUT,
                              on_delta=_vision_guard).strip()
        except _VisionRunaway as e:
            _log.warning("视觉模型 %s 输出失控（%d 字），截断保留前面的内容", model, len(e.text))
            text = _trim_runaway(e.text)
            if len(text) < 50:
                raise VisionUnavailable("视觉模型输出异常") from e
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            _vision_offline_until = time.monotonic() + _VISION_OFFLINE_BACKOFF
            raise VisionUnavailable("连不上服务商") from e
        except (openai.NotFoundError, openai.BadRequestError, openai.PermissionDeniedError) as e:
            # 模型不存在 / 不支持图片输入：换下一个候选
            _log.info("视觉模型 %s 不可用：%s", model, e)
            last = e
            continue
        except Exception as e:
            raise VisionUnavailable(f"图片识别失败：{e}") from e
        if text:
            return text
    raise VisionUnavailable(f"没有可用的视觉模型{f'（{last}）' if last else ''}")


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
