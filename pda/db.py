# -*- coding: utf-8 -*-
"""SQLite 存储：documents / chunks 表 + FTS5 全文索引。

FTS5 用 trigram 分词器：中文没有空格分词，unicode61 会把整段连续汉字当成一个
token，按词查询基本命中不了；trigram 按 3 字滑窗建索引，中英文子串都能命中。
"""
import re
import sqlite3
import time
from contextlib import contextmanager

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id),
    seq INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""

# V1.1 新增列：记录来源文件（原路径 + 修改时间 + 大小），用于重复收录判断
# V1.2 新增列：tags（AI 自动标签，逗号分隔）
_DOC_EXTRA_COLUMNS = {
    "source_path": "TEXT NOT NULL DEFAULT ''",
    "source_mtime": "REAL NOT NULL DEFAULT 0",
    "source_size": "INTEGER NOT NULL DEFAULT 0",
    "tags": "TEXT NOT NULL DEFAULT ''",
    # 1 = 解析降级为"仅归档、按文件名索引"（如 OCR 组件缺失）：再次收录同一文件时重新解析
    "archived_only": "INTEGER NOT NULL DEFAULT 0",
}


_FTS_TOKENIZE = "trigram"


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    # 入库 worker / 问答 worker / GUI 线程各自开连接：WAL 让读写互不阻塞，
    # busy_timeout 放宽到 30 秒，避免写锁竞争时直接报 database is locked
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def connect():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        if row is not None and _FTS_TOKENIZE not in (row["sql"] or ""):
            # 老库用的是 unicode61：删掉按 trigram 重建（content 表是 chunks，数据不丢）
            conn.execute("DROP TABLE chunks_fts")
            row = None
        if row is None:
            conn.execute(
                "CREATE VIRTUAL TABLE chunks_fts USING fts5("
                f"text, content='chunks', content_rowid='id', tokenize='{_FTS_TOKENIZE}')"
            )
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        # 老库迁移：补 V1.1 新增列
        existing = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        for col, decl in _DOC_EXTRA_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {decl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_path)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
        conn.commit()
    finally:
        conn.close()


def add_document_with_chunks(title: str, file_path: str, ext: str, size: int,
                             summary: str, chunks: list,
                             source_path: str = "", source_mtime: float = 0.0,
                             source_size: int = 0, archived_only: bool = False) -> tuple:
    """在同一事务里写入文档、全部 chunks 与 FTS 索引，返回 (doc_id, [chunk_id...])。

    任一步失败整体回滚，不会留下"有文档记录、缺 chunks"的半成品。
    """
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (title, file_path, ext, size, chunk_count, summary,"
            " created_at, source_path, source_mtime, source_size, archived_only)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, file_path, ext, size, len(chunks), summary, time.time(),
             source_path, source_mtime, source_size, 1 if archived_only else 0),
        )
        doc_id = cur.lastrowid
        chunk_ids = []
        for seq, text in enumerate(chunks):
            cur = conn.execute(
                "INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)",
                (doc_id, seq, text),
            )
            chunk_ids.append(cur.lastrowid)
            conn.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text)
            )
        return doc_id, chunk_ids


def find_document_by_source(source_path: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, file_path, source_mtime, source_size, archived_only"
            # NOCASE：老库存的是未 normcase 的原始大小写路径
            " FROM documents WHERE source_path = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1",
            (source_path,),
        ).fetchone()
    return dict(row) if row else None


def delete_document(doc_id: int) -> str | None:
    """删除文档及其全部 chunks 与 FTS 索引，返回归档文件路径（调用方负责删文件）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT file_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            return None
        # external content 表按行删索引（旧实现每次 'rebuild' 全量重建，库越大越慢且长时间持锁）
        for c in conn.execute("SELECT id, text FROM chunks WHERE doc_id = ?", (doc_id,)):
            conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                (c["id"], c["text"]),
            )
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return row["file_path"]


def list_recent_documents(limit: int = 20) -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, file_path, chunk_count, summary, created_at, tags"
            " FROM documents ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_document_tags(doc_id: int, tags: list):
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET tags = ? WHERE id = ?",
            (",".join(tags), doc_id),
        )


def find_documents_by_term(term: str) -> list:
    """按标题/标签模糊匹配文档（限定范围提问用）。"""
    # 用 ! 作 LIKE 转义符，避免 term 里的 % / _ 被当作通配符
    like = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, tags FROM documents"
            " WHERE title LIKE ? ESCAPE '!' OR tags LIKE ? ESCAPE '!'",
            (like, like),
        ).fetchall()
    return [dict(r) for r in rows]


def get_chunk(chunk_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT c.id, c.doc_id, c.seq, c.text, d.title, d.file_path"
            " FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id = ?",
            (chunk_id,),
        ).fetchone()
    return dict(row) if row else None


def get_chunks_by_ids(chunk_ids: list) -> list:
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT c.id, c.doc_id, c.seq, c.text, d.title, d.file_path"
            f" FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in chunk_ids if i in by_id]


_CJK_RUN = re.compile("[㐀-鿿豈-﫿]+")
_WORD = re.compile(r"[0-9A-Za-z_\-.]+")


def _fts_terms(query: str) -> list:
    """把查询拆成 trigram 能匹配的检索词。

    - 连续汉字按 3 字滑窗切（"发布会定在" → 发布会/布会定/会定在），短于 3 字的整段丢弃
      （trigram 无法匹配 1-2 字，这部分交给向量检索）
    - 英文/数字/编号按词取，长度 >= 3
    """
    terms = []
    for run in _CJK_RUN.findall(query):
        terms.extend(run[i:i + 3] for i in range(len(run) - 2))
    terms.extend(w for w in _WORD.findall(query) if len(w) >= 3)
    seen = set()
    return [t for t in terms if not (t in seen or seen.add(t))][:32]


def fts_search(query: str, top_k: int = 5) -> list:
    """FTS5 关键词检索，返回 chunk 行（含文档信息）。查询失败时抛异常由调用方处理。"""
    terms = _fts_terms(query)
    if not terms:
        return []
    # 检索词作为短语加双引号；词内的双引号按 FTS5 语法写成两个
    match_q = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.doc_id, c.seq, c.text, d.title, d.file_path,"
            " bm25(chunks_fts) AS score"
            " FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
            " JOIN documents d ON d.id = c.doc_id"
            " WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (match_q, top_k),
        ).fetchall()
    return [dict(r) for r in rows]
