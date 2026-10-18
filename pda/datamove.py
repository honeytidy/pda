# -*- coding: utf-8 -*-
"""数据目录迁移：把整个 data/（库、向量、归档原件、笔记）搬到用户指定的目录。

流程（设置界面发起，下次启动执行）：
  1. 设置里选好新目录 → check_target() 校验 → schedule() 把 {from, to, mode} 写进
     pda_config.json 的 data_dir_move，然后重启程序
  2. 新进程拿到单实例锁之后、打开 SQLite / chroma 之前调用 run_pending()：
     复制文件 → 改写库里记录的归档路径 → 配置 data_dir 指向新目录
  运行中的进程开着 SQLite 连接和 chroma 客户端（hnsw 索引可能还没刷盘），
  所以复制放在启动阶段做，而不是在设置界面里当场搬。

mode="switch"：新目录里已经有一个知识库（换电脑 / 重装后指回去），只切换不复制。
原目录默认保留，由调用方询问用户是否删除。
"""
import os
import shutil
import sqlite3
from pathlib import Path

from . import config

# 运行时文件：属于当时的实例，不随数据走
_SKIP_FILES = {".instance.lock", "ipc_token", "ipc_token.tmp"}
# 选中的文件夹里已有别的文件时，在里面建这个子目录存放
SUBDIR_NAME = "PDA知识库"


class MoveError(Exception):
    pass


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _is_within(child, parent) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def is_library(path) -> bool:
    return (Path(path) / "pda.db").is_file()


def _is_empty_dir(path: Path) -> bool:
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def dir_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name in _SKIP_FILES:
                continue
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def check_target(chosen, current=None) -> dict:
    """校验用户选中的目录，返回 {target, mode}；不可用时抛 MoveError（消息直接给用户看）。

    - 选中目录里已有知识库（pda.db）：mode="switch"，只切换
    - 选中目录为空或不存在：直接用它
    - 选中目录里有别的文件：在里面建 PDA知识库\\ 子目录（不把库文件和用户文件混在一起）
    """
    current = Path(current or config.DATA_DIR)
    chosen = Path(chosen)
    if not chosen.is_absolute():
        raise MoveError("请选择一个完整路径的文件夹")
    if str(chosen).startswith("\\\\"):
        raise MoveError("不支持网络路径：SQLite 数据库放在网络共享上容易损坏")
    if _norm(chosen) == _norm(current):
        raise MoveError("这就是当前的数据目录")
    if _is_within(chosen, current):
        raise MoveError("新位置不能在当前数据目录里面")

    if is_library(chosen):
        return {"target": str(chosen), "mode": "switch"}
    target = chosen
    if chosen.exists() and not (chosen.is_dir() and _is_empty_dir(chosen)):
        if not chosen.is_dir():
            raise MoveError("选中的不是文件夹")
        target = chosen / SUBDIR_NAME
        if is_library(target):
            return {"target": str(target), "mode": "switch"}
        if target.exists() and not _is_empty_dir(target):
            raise MoveError(f"{target} 已存在且不是空文件夹，请换一个位置")
    if _is_within(current, target):
        raise MoveError("新位置不能是当前数据目录的上级目录")

    # 可写性：先建目录再试写一个文件（只读介质、无权限的目录在这里就拦下）
    created = not target.exists()
    probe = target / ".pda_write_test"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise MoveError(f"无法写入 {target}：{e}") from e
    finally:
        if created:
            try:
                target.rmdir()  # 校验时不留空目录，真正迁移时再建
            except OSError:
                pass

    need = dir_size(current) if current.is_dir() else 0
    try:
        free = shutil.disk_usage(target if target.exists() else _existing_parent(target)).free
    except OSError:
        free = None
    if free is not None and free < need + 50 * 1024 * 1024:
        raise MoveError(f"目标磁盘剩余空间不足：需要约 {_fmt_size(need)}，剩余 {_fmt_size(free)}")
    return {"target": str(target), "mode": "move", "size": need}


def _existing_parent(path: Path) -> Path:
    for p in [path, *path.parents]:
        if p.exists():
            return p
    return path


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


fmt_size = _fmt_size


# ---------- 待执行的迁移（存在 pda_config.json 里，跨一次重启） ----------

def schedule(target: str, mode: str):
    config._update_config_file(
        data_dir_move={"from": str(config.DATA_DIR), "to": str(target), "mode": mode}
    )


def pending() -> dict | None:
    """有待执行的迁移且来源就是当前数据目录时返回它（来源对不上说明配置被改过，作废）。"""
    if config.data_dir_from_env():
        return None
    move = config._read_config_file().get("data_dir_move")
    if not isinstance(move, dict):
        return None
    src, dst = move.get("from"), move.get("to")
    if not (isinstance(src, str) and isinstance(dst, str) and os.path.isabs(dst)):
        return None
    if _norm(src) != _norm(config.DATA_DIR):
        return None
    return {"from": src, "to": dst, "mode": "switch" if move.get("mode") == "switch" else "move"}


def cancel_pending():
    config._update_config_file(data_dir_move=None)


def _commit(target: str):
    """配置指向新目录（目标就是默认位置时删掉 data_dir 键），清掉待迁移标记。"""
    is_default = _norm(target) == _norm(config.DEFAULT_DATA_DIR)
    config._update_config_file(data_dir=None if is_default else str(target), data_dir_move=None)
    config.set_data_dir(target)


# ---------- 执行 ----------

def _copy_tree(src: Path, dst: Path, progress=None):
    """逐文件复制（便于报进度）；progress(done_bytes, total_bytes, name)。"""
    total = dir_size(src)
    done = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out_dir = dst if rel == "." else dst / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name in _SKIP_FILES:
                continue
            s = os.path.join(root, name)
            shutil.copy2(s, out_dir / name)
            try:
                done += os.path.getsize(s)
            except OSError:
                pass
            if progress:
                progress(done, total, name)


def remap_path(path: str, old_root: str, new_root: str) -> str:
    """old_root 下的路径换到 new_root 下；不在 old_root 下的原样返回。大小写不敏感。"""
    if not path:
        return path
    old = os.path.abspath(old_root).rstrip(os.sep)
    p = os.path.abspath(path)
    if os.path.normcase(p).startswith(os.path.normcase(old) + os.sep):
        new = str(Path(new_root) / p[len(old) + 1:])
        # source_path 存的是 normcase 过的路径：保持同样的形式，去重查询才对得上
        return os.path.normcase(new) if path == os.path.normcase(path) else new
    return path


def rewrite_db_paths(db_path, old_root: str, new_root: str) -> int:
    """documents 表里的归档路径 file_path、笔记来源 source_path 改到新目录。返回改动行数。"""
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        rows = conn.execute("SELECT id, file_path, source_path FROM documents").fetchall()
        changes = []
        for doc_id, fp, sp in rows:
            nfp, nsp = remap_path(fp, old_root, new_root), remap_path(sp, old_root, new_root)
            if (nfp, nsp) != (fp, sp):
                changes.append((nfp, nsp, doc_id))
        conn.executemany("UPDATE documents SET file_path = ?, source_path = ? WHERE id = ?", changes)
        conn.commit()
        return len(changes)
    finally:
        conn.close()


def rewrite_vector_paths(collection, old_root: str, new_root: str, batch: int = 500) -> int:
    """chroma 元数据里的 file_path（问答出处链接用）改到新目录。"""
    changed = 0
    offset = 0
    while True:
        got = collection.get(include=["metadatas"], limit=batch, offset=offset)
        ids = got.get("ids") or []
        if not ids:
            break
        upd_ids, upd_meta = [], []
        for cid, meta in zip(ids, got.get("metadatas") or []):
            meta = dict(meta or {})
            fp = meta.get("file_path", "")
            nfp = remap_path(fp, old_root, new_root)
            if nfp != fp:
                meta["file_path"] = nfp
                upd_ids.append(cid)
                upd_meta.append(meta)
        if upd_ids:
            collection.update(ids=upd_ids, metadatas=upd_meta)
            changed += len(upd_ids)
        offset += len(ids)
    return changed


def run_pending(progress=None) -> dict | None:
    """执行待迁移（启动时、打开库之前调用）。没有待迁移返回 None。

    返回 {ok, mode, from, to, error?}。失败时清掉本次复制出的文件，数据目录保持原样，
    待迁移标记也清掉（避免每次启动都重试、反复失败）。
    """
    move = pending()
    if move is None:
        return None
    src, dst = Path(move["from"]), Path(move["to"])
    result = {"ok": False, "mode": move["mode"], "from": str(src), "to": str(dst)}

    if move["mode"] == "switch":
        if not is_library(dst):
            cancel_pending()
            result["error"] = f"{dst} 里没有找到知识库，未切换"
            return result
        _commit(str(dst))
        result["ok"] = True
        return result

    created = not dst.exists()
    if not created and not _is_empty_dir(dst):
        cancel_pending()
        result["error"] = f"{dst} 已不是空文件夹，为避免覆盖其中的文件，未迁移"
        return result
    try:
        dst.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            _copy_tree(src, dst, progress)
        if not (dst / "pda.db").exists():
            (dst / "pda.db").touch()  # 空文件即空库；也作为"这里是知识库"的标志
        rewrite_db_paths(dst / "pda.db", str(src), str(dst))
        # chroma 指向新目录后再打开（本进程此前没打开过 chroma）
        config.set_data_dir(dst)
        from . import vectorstore

        rewrite_vector_paths(vectorstore.get_collection(), str(src), str(dst))
        _commit(str(dst))
    except Exception as e:
        config.set_data_dir(src)
        _discard_copy(dst, created)
        try:
            cancel_pending()
        except Exception:
            pass
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    result["ok"] = True
    return result


def _discard_copy(dst: Path, created: bool):
    """迁移失败：删掉复制到一半的文件（目标原本是空目录或不存在，里面都是本次复制的）。"""
    from . import vectorstore

    vectorstore.reset_client()
    if created:
        shutil.rmtree(dst, ignore_errors=True)
    else:
        for child in list(dst.iterdir()) if dst.is_dir() else []:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass


def remove_old(path) -> str | None:
    """迁移成功后用户同意删除原目录。返回错误信息，成功返回 None。"""
    path = Path(path)
    if _is_within(config.DATA_DIR, path) or not is_library(path):
        return "未删除：该目录看起来不是旧的数据目录"
    errors = []
    shutil.rmtree(path, onexc=lambda f, p, e: errors.append(p))
    return f"有 {len(errors)} 个文件未能删除，可手动删除 {path}" if errors else None
