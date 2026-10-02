# -*- coding: utf-8 -*-
"""入库管道：归档原文件 → 解析 → 切块 → embedding → 写 SQLite + chroma。

供 UI 在后台线程调用；ingest_files 返回每个文件的入库结果。
"""
import hashlib
import logging
import os
import shutil
import stat
import threading
from pathlib import Path

from . import config, db, embeddings, llm, parsers, vectorstore
from .chunking import chunk_text


# 进程内所有入库（拖放 / 监控 / IPC / 热键 / 网页）串行执行：查重→删旧→写入
# 不是原子操作，并发时会产生重复条目或多条记录共用一个归档文件
_INGEST_LOCK = threading.Lock()

_log = logging.getLogger(__name__)

# 拖入文件夹时不进入的目录：版本库、依赖、构建产物、缓存（成千上万个无意义文件）
_SKIP_DIRS = {"node_modules", "__pycache__", "$recycle.bin", "system volume information",
              "venv", ".venv", "site-packages", "dist-packages"}
_HIDDEN_ATTRS = (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
                 | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4))

# 解析不出文本、只能"仅归档"的文件超过这个大小不收录（视频、镜像等整份复制进 data/ 只会占满磁盘）
ARCHIVE_ONLY_MAX = 100 * 1024 * 1024

# embedding + 写向量按批进行：超大文档不必一次把全部向量放进内存
_EMBED_BATCH = 512


def _is_hidden(path: str, name: str) -> bool:
    if name.startswith("."):
        return True
    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0) & _HIDDEN_ATTRS)
    except OSError:
        return False


def collect_files(paths: list) -> list:
    """展开传入的文件/文件夹路径，递归收集文件（不限制类型）。

    展开文件夹时跳过隐藏/系统文件与目录（.git 等）和依赖/缓存目录；直接传入的文件不过滤。
    无权限的子目录跳过（os.walk onerror 忽略），不会中断整批。
    """
    files = []
    for p in paths:
        path = Path(p)
        try:
            if path.is_dir():
                for root, dirs, names in os.walk(path, onerror=lambda e: None):
                    dirs[:] = sorted(
                        d for d in dirs
                        if d.lower() not in _SKIP_DIRS
                        and not _is_hidden(os.path.join(root, d), d)
                    )
                    for name in sorted(names):
                        child = os.path.join(root, name)
                        if os.path.isfile(child) and not _is_hidden(child, name):
                            files.append(child)
            elif path.is_file():
                files.append(str(path))
        except OSError:
            continue
    return files


def normalize_source(path: str) -> str:
    """来源路径规范化：Windows 路径不区分大小写，不同入口拿到的大小写/分隔符可能不同。"""
    return os.path.normcase(os.path.abspath(path))


def _is_inside_data_dir(path: str) -> bool:
    """data/ 下的文件（归档、笔记之外的库文件等）不能再被收录（监控目录包含 data/ 时防循环）。"""
    data = os.path.normcase(os.path.abspath(config.DATA_DIR))
    src = normalize_source(path)
    notes = os.path.normcase(os.path.abspath(config.NOTES_DIR))
    if src.startswith(notes + os.sep):
        return False  # 笔记目录是正常收录来源
    return src == data or src.startswith(data + os.sep)


def _archive(path: str, title: str) -> Path:
    """复制到 data/files/，重名加序号。用 O_EXCL 占位，避免两个文档抢到同一个归档名。"""
    config.ensure_dirs()
    base = Path(config.FILES_DIR) / title
    stem, suffix = base.stem, base.suffix
    n = 0
    while True:
        dest = base if n == 0 else Path(config.FILES_DIR) / f"{stem}_{n}{suffix}"
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            n += 1
    try:
        shutil.copy2(path, dest)
    except BaseException:
        _safe_remove(str(dest))
        raise
    return dest


def ingest_file(path: str) -> dict:
    """入库单个文件，返回 {ok, title, doc_id?, chunk_count?, summary?, error?, skipped?, replaced?}

    重复收录策略（覆盖更新）：按来源路径去重——
    - 来源路径 + 修改时间 + 大小完全一致：跳过（skipped=True）
    - 修改时间变了但提取出的文本与上次一致：只更新记录的时间/大小，跳过（skipped=True）
    - 来源路径相同但内容变化：新版本解析、embedding、写库全部成功后，才删除旧记录
      （含 chunks/FTS/向量/归档文件）；新版本失败时旧版本保留（replaced=True）
    任一步失败都会回滚本次写入（SQLite 事务 + 删除本次向量与归档），下次可重新收录。
    """
    with _INGEST_LOCK:
        result = _ingest_file_locked(path)
    # AI 自动标签放在锁外：断网时每个文件最多要等几十秒超时，持锁会让整批入库
    # 以及热键/监控/IPC 等所有入口一起排队
    tag_text = result.pop("_tag_text", None)
    if tag_text:
        tags = llm.generate_tags(tag_text)  # 无 key / auto_tags=false 时返回 []；失败不影响入库
        if tags:
            try:
                db.set_document_tags(result["doc_id"], tags)
                result["tags"] = tags
            except Exception:
                # 文档可能刚被另一次收录替换掉：标签丢了无所谓
                _log.warning("写入标签失败 doc_id=%s", result.get("doc_id"), exc_info=True)
    return result


def _ingest_file_locked(path: str) -> dict:
    title = os.path.basename(path)
    if _is_inside_data_dir(path):
        return {"ok": True, "title": title, "skipped": True}
    source = normalize_source(path)
    src_mtime = os.path.getmtime(path)
    src_size = os.path.getsize(path)

    existing = db.find_document_by_source(source)
    unchanged = bool(existing) and (abs(existing["source_mtime"] - src_mtime) < 1e-6
                                    and existing["source_size"] == src_size)
    # 上次解析降级为"仅归档"（如当时 OCR 组件缺失）的文件不跳过，重新尝试解析
    if unchanged and not existing.get("archived_only"):
        return {"ok": True, "title": title, "skipped": True, "doc_id": existing["id"]}

    archived_only = False
    try:
        text = parsers.extract_text(path)
    except parsers.ParseError as e:
        # 二进制 / 扫描件 / 空文档 / 图片无文字：归档原件，按文件名建索引（可检索到、可打开原件）
        reason = "未能提取文本内容" if isinstance(e, parsers.BinaryFileError) else str(e)
        text = f"{title}\n（此文件{reason}，已归档原件，可按文件名检索、点击出处打开）"
        archived_only = True
    except Exception as e:
        return {"ok": False, "title": title, "error": f"解析失败：{e}"}

    if archived_only and src_size > ARCHIVE_ONLY_MAX:
        return {"ok": False, "title": title,
                "error": f"无法提取文本且文件过大（超过 {ARCHIVE_ONLY_MAX // (1024 * 1024)}MB），未收录"}

    content_hash = hashlib.sha1(text.encode("utf-8", errors="surrogatepass")).hexdigest()
    if (existing and not archived_only and not existing.get("archived_only")
            and existing.get("content_hash") == content_hash):
        # 内容没变（touch / 打开后原样保存 / 同步盘回写）：不重建索引
        db.update_source_stamp(existing["id"], src_mtime, src_size)
        return {"ok": True, "title": title, "skipped": True, "doc_id": existing["id"]}

    chunks = chunk_text(text)
    if not chunks:
        chunks = chunk_text(f"{title}\n（文档内容为空，已归档原件）")
        archived_only = True

    if archived_only and existing:
        if unchanged:
            # 仍然只能降级：旧记录本来就是"仅归档"，没必要删了重建
            return {"ok": True, "title": title, "skipped": True, "doc_id": existing["id"],
                    "archived_only": True}
        if not existing.get("archived_only"):
            # 新版本解析不出文本（OCR 组件缺失 / 临时空文档 / 公式无缓存值等）：
            # 不拿"按文件名索引"覆盖旧的完整索引，保留旧版本
            return {"ok": True, "title": title, "skipped": True, "doc_id": existing["id"],
                    "kept_old": True,
                    "warning": "新版本未能提取文本，已保留之前收录的版本"}

    # 先加载模型（最容易失败的一步：模型下载/onnx），失败时什么都还没写
    embeddings.get_model()

    summary = " ".join(text.split())[:80]  # 压掉 \r\n / 制表符等空白
    ext = Path(path).suffix.lower()
    dest = _archive(path, title)
    doc_id = None
    try:
        doc_id, chunk_ids = db.add_document_with_chunks(
            title, str(dest), ext, src_size, summary, chunks,
            source_path=source, source_mtime=src_mtime, source_size=src_size,
            archived_only=archived_only, content_hash=content_hash,
        )
        for start in range(0, len(chunks), _EMBED_BATCH):
            part = chunks[start:start + _EMBED_BATCH]
            vectors = embeddings.embed(part)
            vectorstore.add_chunks([
                {
                    "chunk_id": chunk_ids[start + i],
                    "doc_id": doc_id,
                    "seq": start + i,
                    "text": chunk,
                    "title": title,
                    "file_path": str(dest),
                    "vector": vector,
                }
                for i, (chunk, vector) in enumerate(zip(part, vectors))
            ])
    except BaseException:
        # 回滚：SQLite 已提交的记录、可能写了一部分的向量、归档文件
        if doc_id is not None:
            _remove_document(doc_id, str(dest))
        else:
            _safe_remove(str(dest))
        raise

    # 新版本已完整入库，再删旧版本。删旧失败不能把本次报成"收录失败"（新版本
    # 已可用），记日志即可；SQLite 记录一定会删（_remove_document 的 finally）
    replaced = False
    if existing:
        try:
            _remove_document(existing["id"], existing["file_path"])
        except Exception:
            _log.warning("删除旧版本失败 doc_id=%s", existing["id"], exc_info=True)
        replaced = True

    return {
        "ok": True,
        "title": title,
        "doc_id": doc_id,
        "chunk_count": len(chunks),
        "summary": summary,
        "replaced": replaced,
        "archived_only": archived_only,
        "tags": [],
        # 由 ingest_file 释放入库锁后生成标签（仅归档的文件不生成）
        "_tag_text": None if archived_only else text,
    }


def _safe_remove(path: str):
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            # 归档文件被占用（如正用 Word 打开）：留下孤儿文件，不影响库的一致性
            _log.warning("删除归档文件失败：%s", path, exc_info=True)


def _remove_document(doc_id: int, archive_path: str):
    """删除文档记录（SQLite + FTS + 向量库 + 归档文件）。"""
    try:
        vectorstore.delete_document_chunks(doc_id)
    finally:
        db.delete_document(doc_id)
        _safe_remove(archive_path)


def remove_document(doc_id: int) -> bool:
    """用户手动移除文档：删索引（SQLite/FTS/向量）和 data/files 下的归档副本。

    用户电脑上的原文件不动；来源是本程序生成的笔记（data/notes/ 下的剪贴板笔记、
    网页、截图）时一并删除，否则会留下无人引用的文件。文档已不存在返回 False。
    """
    with _INGEST_LOCK:  # 与入库串行：避免删到一半时同一文件正在覆盖更新
        doc = db.get_document(doc_id)
        if doc is None:
            return False
        _remove_document(doc_id, doc["file_path"])
        src = doc.get("source_path") or ""
        notes = os.path.normcase(os.path.abspath(config.NOTES_DIR))
        if src and normalize_source(src).startswith(notes + os.sep):
            _safe_remove(src)
    return True


def new_note_path(prefix: str, suffix: str) -> str:
    """data/notes/<prefix>_YYYYMMDD_HHMMSS<suffix>，重名加序号（只生成路径，不创建文件）。"""
    import time as _time

    config.ensure_dirs()
    stamp = _time.strftime('%Y%m%d_%H%M%S')
    path = Path(config.NOTES_DIR) / f"{prefix}_{stamp}{suffix}"
    n = 1
    while path.exists():
        path = Path(config.NOTES_DIR) / f"{prefix}_{stamp}_{n}{suffix}"
        n += 1
    return str(path)


def save_clipboard_note(text: str) -> str:
    """把剪贴板文本存为笔记文件（data/notes/笔记_YYYYMMDD_HHMMSS.txt），返回路径。"""
    path = new_note_path("笔记", ".txt")
    Path(path).write_text(text, encoding="utf-8")
    return path


def _is_sharing_violation(e: BaseException) -> bool:
    # WinError 32（被另一进程占用）/ 33（部分被锁定）：写入方还没释放，稍后多半就好了
    return isinstance(e, OSError) and getattr(e, "winerror", None) in (32, 33)


def _ingest_with_retry(path: str, attempts: int = 3, delay: float = 1.5) -> dict:
    import time as _time

    for i in range(attempts):
        try:
            return ingest_file(path)
        except Exception as e:
            if i == attempts - 1 or not _is_sharing_violation(e):
                raise
            _time.sleep(delay)


def ingest_files(paths: list, progress_cb=None) -> list:
    """批量入库，progress_cb(当前文件名, 已完成数, 总数) 可选。返回结果列表。"""
    files = collect_files(paths)
    results = []
    for i, f in enumerate(files):
        if progress_cb:
            progress_cb(f, i, len(files))
        try:
            results.append(_ingest_with_retry(f))
        except Exception as e:
            # 单文件异常（如文件被占用 WinError 32）不应中断整批，也不让 worker 崩
            results.append(
                {"ok": False, "title": os.path.basename(f), "error": f"收录失败：{e}"}
            )
    return results
