# -*- coding: utf-8 -*-
"""chromadb 本地持久化封装：collection "pda_chunks"，cosine 距离。"""
import threading

import chromadb
from chromadb.config import Settings

from . import config

_COLLECTION = "pda_chunks"

_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            config.ensure_dirs()
            # 关闭 chromadb 匿名遥测：本地知识库不向第三方上报任何使用数据
            _client = chromadb.PersistentClient(
                path=str(config.CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False),
            )
        return _client


def get_collection():
    return _get_client().get_or_create_collection(
        name=_COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def add_chunks(chunk_rows: list):
    """chunk_rows: [{chunk_id, doc_id, seq, text, title, file_path, vector}]"""
    if not chunk_rows:
        return
    collection = get_collection()
    collection.add(
        ids=[f"{r['doc_id']}_{r['seq']}" for r in chunk_rows],
        embeddings=[r["vector"] for r in chunk_rows],
        documents=[r["text"] for r in chunk_rows],
        metadatas=[
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "file_path": r["file_path"],
            }
            for r in chunk_rows
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
    if collection.count() == 0:
        return []
    kwargs = {}
    if doc_ids is not None:
        if not doc_ids:
            return []
        kwargs["where"] = {"doc_id": {"$in": [int(d) for d in doc_ids]}}
    result = collection.query(
        query_embeddings=[vector], n_results=min(top_k, max(collection.count(), 1)),
        **kwargs,
    )
    hits = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        try:
            doc_id, seq = cid.rsplit("_", 1)
            seq = int(seq)
        except ValueError:
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
