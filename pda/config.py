# -*- coding: utf-8 -*-
"""数据目录与 LLM 配置。

数据目录默认 ./data（可用环境变量 PDA_DATA_DIR 覆盖，便于测试隔离）：
  data/files/        归档的原始文件
  data/notes/        剪贴板笔记 / 网页抓取
  data/pda.db        SQLite（documents / chunks / FTS5）
  data/chroma/       chromadb 持久化目录
  data/model_cache/  embedding 模型缓存（软件自包含，不依赖 %TEMP%）

打包（PyInstaller frozen）模式分两种布局：
  便携版：exe 旁有 portable.flag（make_release.py 放入）或已有 data/ 目录（老版本绿色包），
          data/ 与 pda_config.json 落在 exe 旁边，整个目录可拷贝携带。
  安装版：以上都没有（Inno Setup 安装包），data/ 与 pda_config.json 落在
          %LOCALAPPDATA%\\PDA\\ 下——安装目录可能不可写，卸载/升级也不会动用户数据。
          安装包把内置语义模型放在 <安装目录>\\model_cache，优先使用它，免首次下载。

LLM 配置来源（优先级）：环境变量 PDA_API_KEY / PDA_BASE_URL / PDA_MODEL，
其次 pda_config.json（可用 PDA_CONFIG_PATH 覆盖；设置界面写入的也是它）。
"""
import json
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_portable() -> bool:
    if not FROZEN:
        return True  # 源码运行：数据留在项目目录，行为同便携版
    # 安装包写入的标记优先：旧版安装在安装目录下留过 data\，不能据此误判为便携版
    if (PROJECT_ROOT / "installed.flag").is_file():
        return False
    return (PROJECT_ROOT / "portable.flag").is_file() or (PROJECT_ROOT / "data").is_dir()


PORTABLE = _is_portable()

if PORTABLE:
    APP_HOME = PROJECT_ROOT
else:
    APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PDA"

DATA_DIR = Path(os.environ.get("PDA_DATA_DIR", APP_HOME / "data"))
FILES_DIR = DATA_DIR / "files"
NOTES_DIR = DATA_DIR / "notes"
DB_PATH = DATA_DIR / "pda.db"
CHROMA_DIR = DATA_DIR / "chroma"

_BUNDLED_MODEL_CACHE = PROJECT_ROOT / "model_cache"
if not PORTABLE and "PDA_DATA_DIR" not in os.environ and _BUNDLED_MODEL_CACHE.is_dir():
    MODEL_CACHE_DIR = _BUNDLED_MODEL_CACHE  # 安装版内置模型（per-user 安装目录，可写）
else:
    MODEL_CACHE_DIR = DATA_DIR / "model_cache"

ERROR_LOG = DATA_DIR / "pda_error.log"

CONFIG_PATH = Path(os.environ.get("PDA_CONFIG_PATH", APP_HOME / "pda_config.json"))

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k2-0905-preview"


def ensure_dirs():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ConfigError(Exception):
    """pda_config.json 存在但无法解析（此时拒绝写回，避免覆盖掉 api_key 等配置）。"""


def _read_config_file(strict: bool = False) -> dict:
    """读取配置文件。utf-8-sig 兼容记事本带 BOM 的保存。

    strict=False（读取场景）：解析失败返回 {}；strict=True（写回前）：抛 ConfigError。
    """
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("顶层不是 JSON 对象")
        return data
    except (OSError, ValueError) as e:  # JSONDecodeError 是 ValueError 子类
        if strict:
            raise ConfigError(f"{CONFIG_PATH.name} 格式错误，请先手工修正：{e}") from e
        return {}


def get_llm_config() -> dict:
    """返回 {api_key, base_url, model}；api_key 可能为 None。"""
    file_cfg = _read_config_file()
    return {
        "api_key": os.environ.get("PDA_API_KEY") or file_cfg.get("api_key"),
        "base_url": os.environ.get("PDA_BASE_URL")
        or file_cfg.get("base_url")
        or DEFAULT_BASE_URL,
        "model": os.environ.get("PDA_MODEL") or file_cfg.get("model") or DEFAULT_MODEL,
    }


def get_watch_folders() -> list:
    """监控文件夹列表（来自 pda_config.json 的 watch_folders）。"""
    folders = _read_config_file().get("watch_folders") or []
    # 手工编辑时漏写方括号（"watch_folders": "D:/x"）：按单个目录处理，
    # 否则字符串会被逐字符遍历，"\\" 还会被当成当前盘根目录整盘监控
    if isinstance(folders, str):
        folders = [folders]
    elif not isinstance(folders, (list, tuple)):
        return []
    return [f.strip() for f in folders if isinstance(f, str) and f.strip()]


def _update_config_file(**values):
    """写回 pda_config.json（只改传入的键，保留其他配置项；值为 None 表示删除该键）。

    配置文件损坏时抛 ConfigError 而不是用空配置覆盖；写入走临时文件 + 替换，
    中途失败不会留下半截文件。
    """
    cfg = _read_config_file(strict=True)
    for k, v in values.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def save_watch_folders(folders: list):
    _update_config_file(watch_folders=list(folders))


def get_llm_file_config() -> dict:
    """设置界面用：只看 pda_config.json 里的值（不含环境变量与默认值）。"""
    cfg = _read_config_file()
    return {
        "api_key": cfg.get("api_key") or "",
        "base_url": cfg.get("base_url") or "",
        "model": cfg.get("model") or "",
    }


def llm_env_overrides() -> list:
    """哪些 LLM 配置被环境变量覆盖（设置界面提示用户：改了文件也不生效）。"""
    return [n for n in ("PDA_API_KEY", "PDA_BASE_URL", "PDA_MODEL") if os.environ.get(n)]


def save_llm_config(api_key: str, base_url: str, model: str, auto_tags: bool):
    """空字符串 = 删除该键（回落到默认值 / 无 key 模式）。"""
    _update_config_file(
        api_key=api_key.strip() or None,
        base_url=base_url.strip() or None,
        model=model.strip() or None,
        auto_tags=bool(auto_tags),
    )


def auto_tags_enabled() -> bool:
    """入库时是否把文档开头发给 LLM 生成标签（pda_config.json 的 auto_tags，默认开）。"""
    return _parse_bool(_read_config_file().get("auto_tags", True), default=True)


def _parse_bool(value, default: bool) -> bool:
    """宽松解析手写配置里的布尔值："false" / "0" / "no" / "off" / 0 都视为关闭。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("false", "0", "no", "off", "n", "否", "关", "关闭", ""):
            return False
        if v in ("true", "1", "yes", "on", "y", "是", "开", "开启"):
            return True
    return default


# ---------- 开机自启（HKCU Run 键，per-user 无需管理员） ----------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "PDA"


def launch_command() -> str:
    """开机自启/右键菜单用的启动命令：打包后用 exe，开发模式用 pythonw + run.py。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{exe}" "{PROJECT_ROOT / "run.py"}"'


def autostart_command() -> str:
    """开机自启命令：带 --minimized，启动后只留托盘图标。"""
    return launch_command() + " --minimized"


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
    except FileNotFoundError:
        return False
    value = str(value).strip().lower()
    if value == autostart_command().strip().lower():
        return True
    # 旧版本写的是不带 --minimized 的命令：仍视为已启用（下次保存设置时改写为新命令）
    if value == launch_command().strip().lower():
        return True
    # 绿色版搬家后旧值指向失效路径：视为未启用，用户再勾选保存即可改写为新路径
    return False


def set_autostart(enabled: bool):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
