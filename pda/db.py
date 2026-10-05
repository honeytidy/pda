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
    # 提取出的文本的 sha1：只改了修改时间（touch / 打开后原样保存 / 同步盘回写）时不重建索引
    "content_hash": "TEXT NOT NULL DEFAULT ''",
}


_FTS_TOKENIZE = "trigram"

# WAL 是持久化到库文件的设置，每个进程设一次即可（不必每次开连接都执行）
_wal_ready = False


def _connect() -> sqlite3.Connection:
    global _wal_ready
    if not _wal_ready:
        config.ensure_dirs()
    # 入库 worker / 问答 worker / GUI 线程各自开连接：WAL 让读写互不阻塞，
    # timeout 放宽到 30 秒（即 busy_timeout），避免写锁竞争时直接报 database is locked
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    if not _wal_ready:
        conn.execute("PRAGMA journal_mode=WAL")
        _wal_ready = True
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
                             source_size: int = 0, archived_only: bool = False,
                             content_hash: str = "") -> tuple:
    """在同一事务里写入文档、全部 chunks 与 FTS 索引，返回 (doc_id, [chunk_id...])。

    任一步失败整体回滚，不会留下"有文档记录、缺 chunks"的半成品。
    """
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (title, file_path, ext, size, chunk_count, summary,"
            " created_at, source_path, source_mtime, source_size, archived_only, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, file_path, ext, size, len(chunks), summary, time.time(),
             source_path, source_mtime, source_size, 1 if archived_only else 0, content_hash),
        )
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO chunks (doc_id, seq, text) VALUES (?, ?, ?)",
            ((doc_id, seq, text) for seq, text in enumerate(chunks)),
        )
        # FTS 一条语句从 chunks 批量建索引，不必逐行插入
        conn.execute(
            "INSERT INTO chunks_fts (rowid, text) SELECT id, text FROM chunks WHERE doc_id = ?",
            (doc_id,),
        )
        chunk_ids = [r[0] for r in conn.execute(
            "SELECT id FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
        )]
        return doc_id, chunk_ids


def find_document_by_source(source_path: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, file_path, source_mtime, source_size, archived_only, content_hash"
            # NOCASE：老库存的是未 normcase 的原始大小写路径
            " FROM documents WHERE source_path = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1",
            (source_path,),
        ).fetchone()
    return dict(row) if row else None


def update_source_stamp(doc_id: int, source_mtime: float, source_size: int):
    """内容没变、只是修改时间变了：只更新记录的时间/大小，下次直接按签名跳过。"""
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET source_mtime = ?, source_size = ? WHERE id = ?",
            (source_mtime, source_size, doc_id),
        )


def get_document(doc_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, file_path, source_path FROM documents WHERE id = ?", (doc_id,)
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
        conn.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, text)"
            " SELECT 'delete', id, text FROM chunks WHERE doc_id = ?",
            (doc_id,),
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


# 问句里的虚词/疑问词：按它们切开连续汉字，剩下的 2 字段多半是实词（"怎么报销" → 报销）
_FUNCTION_WORDS = re.compile(
    "是什么|为什么|怎么样|有没有|是不是|请问|什么|怎么|如何|哪些|哪个|哪里|多少|是否|"
    "一下|关于|还有|以及|[的了吗呢吧啊么和与及或在是有]"
)


def _short_terms(query: str) -> list:
    """trigram 匹配不到的 2 字中文词（"合同"、"怎么报销"里的"报销"），走 LIKE 子串匹配。"""
    words = []
    for run in _CJK_RUN.findall(query):
        words.extend(w for w in _FUNCTION_WORDS.split(run) if len(w) == 2)
    return list(dict.fromkeys(words))[:8]


def fts_search(query: str, top_k: int = 5, doc_ids: list | None = None) -> list:
    """FTS5 关键词检索，返回 chunk 行（含文档信息）。查询失败时抛异常由调用方处理。

    doc_ids 不为 None 时只在这些文档里检索（在 SQL 里过滤，不是先取全库 top_k 再筛）。
    """
    terms = _fts_terms(query)
    shorts = _short_terms(query)
    if not (terms or shorts) or (doc_ids is not None and not doc_ids):
        return []
    scope_sql, scope_params = "", []
    if doc_ids is not None:
        scope_sql = f" AND c.doc_id IN ({','.join('?' for _ in doc_ids)})"
        scope_params = [int(d) for d in doc_ids]
    rows = []
    with connect() as conn:
        if terms:
            # 检索词作为短语加双引号；词内的双引号按 FTS5 语法写成两个
            match_q = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            rows = conn.execute(
                "SELECT c.id, c.doc_id, c.seq, c.text, d.title, d.file_path,"
                " bm25(chunks_fts) AS score"
                " FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
                " JOIN documents d ON d.id = c.doc_id"
                f" WHERE chunks_fts MATCH ?{scope_sql} ORDER BY score LIMIT ?",
                [match_q, *scope_params, top_k],
            ).fetchall()
        if shorts and len(rows) < top_k:
            # 2 字词 trigram 索引用不上，LIKE 全表扫描：个人知识库规模下可接受；
            # 命中的短词越多排越前
            hit_sql = " + ".join("(instr(c.text, ?) > 0)" for _ in shorts)
            seen = [r["id"] for r in rows]
            seen_sql = f" AND c.id NOT IN ({','.join('?' for _ in seen)})" if seen else ""
            rows += conn.execute(
                "SELECT c.id, c.doc_id, c.seq, c.text, d.title, d.file_path,"
                f" ({hit_sql}) AS hits"
                " FROM chunks c JOIN documents d ON d.id = c.doc_id"
                f" WHERE ({hit_sql}) > 0{scope_sql}{seen_sql}"
                " ORDER BY hits DESC, c.id LIMIT ?",
                [*shorts, *shorts, *scope_params, *seen, top_k - len(rows)],
            ).fetchall()
    return [dict(r) for r in rows]
