# -*- coding: utf-8 -*-
"""chromadb 本地持久化封装：collection "pda_chunks"，cosine 距离。"""
import threading

from . import config

_COLLECTION = "pda_chunks"

# chromadb 单次 add 有上限（client.get_max_batch_size()，SQLite 后端通常几千）；
# 取不到时用这个保守值
_FALLBACK_BATCH = 1000

_client = None
_collection = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            # 延迟导入：chromadb 导入要几秒，不拖慢窗口首次显示
            import chromadb
            from chromadb.config import Settings

            config.ensure_dirs()
            # 关闭 chromadb 匿名遥测：本地知识库不向第三方上报任何使用数据
            _client = chromadb.PersistentClient(
                path=str(config.CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False),
            )
        return _client


def get_collection():
    global _collection
    if _collection is None:
        _collection = _get_client().get_or_create_collection(
            name=_COLLECTION, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def _max_batch() -> int:
    try:
        return max(1, int(_get_client().get_max_batch_size()))
    except Exception:
        return _FALLBACK_BATCH


def add_chunks(chunk_rows: list):
    """chunk_rows: [{chunk_id, doc_id, seq, text, title, file_path, vector}]"""
    if not chunk_rows:
        return
    collection = get_collection()
    step = _max_batch()
    # 分批写：大文档一次 add 超过上限会整篇失败回滚。中途失败由调用方按 doc_id 整体删除
    for i in range(0, len(chunk_rows), step):
        batch = chunk_rows[i:i + step]
        collection.add(
            ids=[f"{r['doc_id']}_{r['seq']}" for r in batch],
            embeddings=[r["vector"] for r in batch],
            documents=[r["text"] for r in batch],
            metadatas=[
                {
                    "chunk_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "file_path": r["file_path"],
                }
                for r in batch
            ],
        )


def delete_document_chunks(doc_id: int):
    """删除某文档在向量库中的全部 chunks。"""
    collection = get_collection()
    if collection.count() == 0:
        return
    collection.delete(where={"doc_id": doc_id})


def search(vector: list, top_k: int = 8, doc_ids: list | None = None) -> list:
    """返回 [{chunk_id, doc_id, seq, text, title, file_path, distance}]

    doc_ids 不为 None 时只检索这些文档的 chunks（限定范围提问）。
    """
    collection = get_collection()
    total = collection.count()
    if total == 0:
        return []
    kwargs = {}
    if doc_ids is not None:
        if not doc_ids:
            return []
        kwargs["where"] = {"doc_id": {"$in": [int(d) for d in doc_ids]}}
    result = collection.query(
        query_embeddings=[vector], n_results=min(top_k, total),
        **kwargs,
    )
    hits = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        try:
            seq = int(cid.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            seq = -1
        hits.append(
            {
                "chunk_id": meta.get("chunk_id"),
                "doc_id": meta.get("doc_id"),
                "seq": seq,
                "text": doc,
                "title": meta.get("title", ""),
                "file_path": meta.get("file_path", ""),
                "distance": dist,
            }
        )
    return hits
