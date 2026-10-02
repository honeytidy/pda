# -*- coding: utf-8 -*-
"""测试环境：数据目录、配置文件指向临时目录（必须在 import pda 之前设置环境变量）。"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="pda_test_"))
os.environ["PDA_DATA_DIR"] = str(_TMP / "data")
os.environ["PDA_CONFIG_PATH"] = str(_TMP / "pda_config.json")
for name in ("PDA_API_KEY", "PDA_BASE_URL", "PDA_MODEL"):
    os.environ.pop(name, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fake_vec(text: str) -> list:
    """确定性的 8 维伪向量：同样的文本得到同样的向量，不需要下载模型。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 + 0.01 for b in digest[:8]]


@pytest.fixture
def fake_embeddings(monkeypatch):
    from pda import embeddings

    monkeypatch.setattr(embeddings, "get_model", lambda: None)
    calls = []

    def embed(texts):
        calls.append(len(texts))
        return [_fake_vec(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed", embed)
    return calls


@pytest.fixture
def store(fake_embeddings):
    """干净的 SQLite + chroma（每个测试清空一次）。"""
    from pda import db, vectorstore

    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM chunks")
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.execute("DELETE FROM documents")
    col = vectorstore.get_collection()
    ids = col.get()["ids"]
    if ids:
        col.delete(ids=ids)
    return fake_embeddings


def pytest_sessionfinish(session, exitstatus):
    import shutil

    try:
        from pda import vectorstore

        vectorstore._client = None
        vectorstore._collection = None
    except Exception:
        pass
    shutil.rmtree(_TMP, ignore_errors=True)
