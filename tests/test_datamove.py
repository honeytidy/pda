# -*- coding: utf-8 -*-
"""数据目录迁移：校验目标、复制、改写库里的归档路径、切换到已有知识库、失败回滚。"""
import os
from pathlib import Path

import pytest

from pda import config, datamove, db, ingest, vectorstore


@pytest.fixture
def moved_back(monkeypatch):
    """模拟没设 PDA_DATA_DIR 的正常运行；结束后把数据目录指回原位置、清掉配置里的 data_dir。"""
    monkeypatch.delenv("PDA_DATA_DIR", raising=False)
    original = config.DATA_DIR
    yield
    vectorstore.reset_client()
    config.set_data_dir(original)
    try:
        config._update_config_file(data_dir=None, data_dir_move=None)
    except config.ConfigError:
        pass


def test_check_target_rules(tmp_path):
    cur = tmp_path / "cur"
    (cur / "files").mkdir(parents=True)
    with pytest.raises(datamove.MoveError):
        datamove.check_target(cur, cur)
    with pytest.raises(datamove.MoveError):
        datamove.check_target(cur / "files", cur)
    # 空目录：直接用
    empty = tmp_path / "empty"
    empty.mkdir()
    assert datamove.check_target(empty, cur) == {"target": str(empty), "mode": "move", "size": 0}
    # 有别的文件：在里面建子目录
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("x")
    plan = datamove.check_target(busy, cur)
    assert plan["target"] == str(busy / datamove.SUBDIR_NAME)
    assert not (busy / datamove.SUBDIR_NAME).exists()  # 校验不留空目录
    # 已有知识库：切换
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "pda.db").write_bytes(b"")
    assert datamove.check_target(lib, cur)["mode"] == "switch"


def test_remap_path():
    old, new = r"C:\Users\a\AppData\Local\PDA\data", r"E:\PDA"
    assert datamove.remap_path(old + r"\files\a.txt", old, new) == r"E:\PDA\files\a.txt"
    # source_path 是 normcase 过的
    src = os.path.normcase(old + r"\notes\n.md")
    assert datamove.remap_path(src, old, new) == os.path.normcase(r"E:\PDA\notes\n.md")
    assert datamove.remap_path(r"D:\docs\a.txt", old, new) == r"D:\docs\a.txt"
    assert datamove.remap_path(old + "x\\a.txt", old, new) == old + "x\\a.txt"  # 前缀相同的兄弟目录


def test_move_and_rewrite_paths(store, tmp_path, moved_back):
    src_doc = tmp_path / "doc.txt"
    src_doc.write_text("迁移测试文档：报销流程需要部门经理签字。", encoding="utf-8")
    r = ingest.ingest_file(str(src_doc))
    assert r["ok"] and r["doc_id"]
    old = config.DATA_DIR
    target = tmp_path / "newlib"

    vectorstore.reset_client()  # 模拟新进程：打开库之前执行迁移
    datamove.schedule(str(target), "move")
    result = datamove.run_pending()
    assert result["ok"], result
    assert config.DATA_DIR == target
    assert datamove.pending() is None
    assert config._configured_data_dir() == str(target)
    assert (target / "pda.db").is_file()
    assert not (target / ".instance.lock").exists()

    doc = db.get_document(r["doc_id"])
    assert Path(doc["file_path"]).parent == target / "files"
    assert Path(doc["file_path"]).is_file()
    assert db.find_document_by_source(ingest.normalize_source(str(src_doc)))["id"] == r["doc_id"]
    metas = vectorstore.get_collection().get(include=["metadatas"])["metadatas"]
    assert metas and all(Path(m["file_path"]).parent == target / "files" for m in metas)
    # 原目录保留，由用户决定是否删除
    assert (old / "pda.db").is_file()


def test_switch_to_existing_library(store, tmp_path, moved_back):
    lib = tmp_path / "existing"
    lib.mkdir()
    (lib / "pda.db").write_bytes(b"")
    vectorstore.reset_client()
    datamove.schedule(str(lib), "switch")
    result = datamove.run_pending()
    assert result["ok"] and result["mode"] == "switch"
    assert config.DATA_DIR == lib


def test_failed_move_keeps_old_dir(store, tmp_path, moved_back, monkeypatch):
    old = config.DATA_DIR
    target = tmp_path / "broken"
    vectorstore.reset_client()
    datamove.schedule(str(target), "move")

    def boom(*a, **k):
        raise OSError("磁盘已满")

    monkeypatch.setattr(datamove, "rewrite_db_paths", boom)
    result = datamove.run_pending()
    assert not result["ok"] and "磁盘已满" in result["error"]
    assert config.DATA_DIR == old
    assert not target.exists()
    assert datamove.pending() is None
    assert config._configured_data_dir() == ""


def test_stale_pending_ignored(tmp_path, moved_back):
    config._update_config_file(data_dir_move={"from": str(tmp_path / "other"), "to": str(tmp_path / "x")})
    assert datamove.pending() is None


def test_unavailable_custom_dir(tmp_path, moved_back):
    config._update_config_file(data_dir=str(tmp_path / "unplugged"))
    assert config.data_dir_unavailable()
    (tmp_path / "unplugged").mkdir()
    assert not config.data_dir_unavailable()
