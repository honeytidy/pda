# -*- coding: utf-8 -*-
"""fastembed 封装：懒加载 BAAI/bge-small-zh-v1.5（384 维，onnxruntime 推理）。

首次加载会从 HuggingFace 下载约 180MB 模型；get_model() 的 on_loading 回调
带 downloading 参数（缓存已存在时为 False），供 UI 区分"下载中"与"加载中"。

大批量（>=8 片段）用线程池并发调用共享模型实例提速：onnxruntime 的
InferenceSession 并发 Run 是线程安全的（已用 numpy.allclose 验证结果与串行
逐位一致），实测 86 片段负载提速约 1.5 倍（onnxruntime 默认 intra-op 已占满
CPU 核，并发 session 调用填补算子间同步空档；intra-op threads=N、独立实例、
fastembed parallel=N 等替代方案实测均更差，见 V1.2 benchmark 记录）。
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 并行参数（实测调优：16 核 CPU，86 片段）
_PARALLEL_MIN = 8   # 少于此片段数直接串行，避免线程开销
_SLICE = 4          # 每个任务的片段数
_MAX_WORKERS = 8

_model = None
_loaded = False
_model_lock = threading.Lock()  # 问答与入库同时首次调用时只加载/下载一次
_on_loading: Callable[[bool], None] | None = None


def set_loading_callback(cb: Callable[[bool], None] | None):
    """cb(downloading)：模型缓存不存在、需要下载时为 True。"""
    global _on_loading
    _on_loading = cb


def is_loaded() -> bool:
    return _loaded


def _model_cached() -> bool:
    from . import config

    cache = config.MODEL_CACHE_DIR
    if not cache.is_dir():
        return False
    return any(
        (cache / name).is_dir()
        for name in ("models--Qdrant--bge-small-zh-v1.5", "fast-bge-small-zh-v1.5")
    )


def get_model():
    global _model, _loaded
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if _on_loading is not None:
            _on_loading(not _model_cached())
        from fastembed import TextEmbedding

        from . import config

        config.ensure_dirs()
        _model = TextEmbedding(
            model_name=_MODEL_NAME, cache_dir=str(config.MODEL_CACHE_DIR)
        )
        _loaded = True
        return _model


def _embed_slice(model, texts: list) -> list:
    return [vec.tolist() for vec in model.embed(texts)]


def embed(texts: list) -> list:
    """texts: list[str] -> list[list[float]]。大批量自动线程池并行。

    异常：pool.map 会把任一子任务的异常原样抛给调用方，不会挂起。
    """
    model = get_model()
    if len(texts) < _PARALLEL_MIN:
        return _embed_slice(model, texts)
    workers = min(_MAX_WORKERS, os.cpu_count() or 4)
    slices = [texts[i:i + _SLICE] for i in range(0, len(texts), _SLICE)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(lambda s: _embed_slice(model, s), slices))
    return [vec for part in parts for vec in part]
