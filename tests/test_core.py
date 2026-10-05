# -*- coding: utf-8 -*-
import os
import time

import pytest


# ---------- 纯函数 ----------

def test_chunk_text_respects_target_size():
    from pda.chunking import chunk_text

    chunks = chunk_text("段落。" * 2000)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert chunk_text("   ") == []


def test_validate_paths(tmp_path):
    from pda import ipc

    ok, reason = ipc.validate_paths([str(tmp_path), "relative\\x", "\\\\server\\share", "C:\\"])
    assert ok == [str(tmp_path)]
    assert reason  # 第一条拒绝原因
    ok, reason = ipc.validate_paths([str(tmp_path / "missing.txt")])
    assert ok == [] and "不存在" in reason


def test_ipc_client_does_not_load_qt():
    import subprocess
    import sys

    code = ("import sys, pda.ipc; "
            "sys.exit(1 if any(m.startswith('PySide6') for m in sys.modules) else 0)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0


def test_parse_scope():
    from pda import qa

    assert qa.parse_scope("在会议纪要里说了什么") == "会议纪要"
    assert qa.parse_scope("现在公司里有多少人") is None
    assert qa.parse_scope("《年度报告》的结论") == "年度报告"


def test_fts_terms():
    from pda import db

    assert db._fts_terms("发布会定在") == ["发布会", "布会定", "会定在"]
    assert db._fts_terms("ab") == []


def test_html_to_text_no_duplicates_and_keeps_loose_text():
    from pda import web

    html = ("<div>开头<ul><li><p>甲</p></li><li>乙</li></ul>"
            "<table><tr><td>x</td><td>y</td></tr></table>结尾</div>")
    text = web._html_to_text(html)
    assert text.count("甲") == 1
    assert "开头" in text and "结尾" in text and "x | y" in text


# ---------- 配置 ----------

def test_api_key_encrypted_on_disk():
    from pda import config

    config.save_llm_config("sk-secret-123", "", "", True)
    raw = config.CONFIG_PATH.read_text(encoding="utf-8")
    assert "sk-secret-123" not in raw
    assert config.get_llm_config()["api_key"] == "sk-secret-123"
    assert config.get_llm_file_config()["api_key"] == "sk-secret-123"
    config.save_llm_config("", "", "", True)
    assert config.get_llm_config()["api_key"] is None


def test_plaintext_key_still_readable():
    import json

    from pda import config

    config.CONFIG_PATH.write_text(json.dumps({"api_key": "sk-plain"}), encoding="utf-8")
    try:
        assert config.get_llm_config()["api_key"] == "sk-plain"
    finally:
        config.CONFIG_PATH.unlink()


# ---------- 入库 / 检索 ----------

def test_ingest_skip_and_content_hash(store, tmp_path):
    from pda import ingest

    f = tmp_path / "note.txt"
    f.write_text("项目发布会定在十月。\n" * 5, encoding="utf-8")
    r1 = ingest.ingest_file(str(f))
    assert r1["ok"] and not r1.get("skipped") and r1["chunk_count"] >= 1

    # 签名完全一致：跳过
    assert ingest.ingest_file(str(f))["skipped"]

    # 只改修改时间：内容哈希一致，不重建（不再调用 embedding）
    calls_before = len(store)
    later = time.time() + 100
    os.utime(f, (later, later))
    r3 = ingest.ingest_file(str(f))
    assert r3["skipped"] and r3["doc_id"] == r1["doc_id"]
    assert len(store) == calls_before

    # 内容变了：覆盖更新
    f.write_text("内容改了：发布会改到十一月。", encoding="utf-8")
    r4 = ingest.ingest_file(str(f))
    assert r4["ok"] and r4["replaced"] and r4["doc_id"] != r1["doc_id"]


def test_large_document_batched(store, tmp_path, monkeypatch):
    from pda import db, ingest, vectorstore

    monkeypatch.setattr(vectorstore, "_max_batch", lambda: 7)
    monkeypatch.setattr(ingest, "_EMBED_BATCH", 10)
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"第{i}段，内容编号{i:05d}。" * 30 for i in range(40)), encoding="utf-8")
    r = ingest.ingest_file(str(f))
    assert r["ok"]
    n = r["chunk_count"]
    assert n > 10
    assert max(store) <= 10  # embedding 分批
    assert vectorstore.get_collection().count() == n
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == n
    # 删除后 FTS / 向量都清干净
    assert ingest.remove_document(r["doc_id"])
    assert vectorstore.get_collection().count() == 0
    assert db.fts_search("内容编号00001") == []


def test_fts_scope_filtered_in_sql(store, tmp_path):
    from pda import db, ingest

    ids = []
    for i in range(30):
        f = tmp_path / f"doc{i}.txt"
        f.write_text(f"关键词字符串出现在文档{i}", encoding="utf-8")
        ids.append(ingest.ingest_file(str(f))["doc_id"])
    target = ids[-1]
    hits = db.fts_search("关键词字符串", top_k=5, doc_ids=[target])
    assert hits and all(h["doc_id"] == target for h in hits)
    assert db.fts_search("关键词字符串", doc_ids=[]) == []


def test_answer_embeds_query_once(store, tmp_path, monkeypatch):
    from pda import ingest, llm, qa

    f = tmp_path / "会议纪要.txt"
    f.write_text("会议决定下周上线。", encoding="utf-8")
    ingest.ingest_file(str(f))
    monkeypatch.setattr(llm, "has_llm", lambda: False)
    before = len(store)
    result = qa.answer("在会议纪要里决定了什么")
    assert result["sources"]
    assert len(store) - before == 1


def test_collect_files_skips_hidden_and_vendor_dirs(tmp_path):
    from pda import ingest

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    for d in (".git", "node_modules", "sub"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "f.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("x", encoding="utf-8")
    names = sorted(os.path.relpath(p, tmp_path) for p in ingest.collect_files([str(tmp_path)]))
    assert names == ["a.txt", os.path.join("sub", "f.txt")]


def test_archive_only_size_limit(store, tmp_path, monkeypatch):
    from pda import ingest

    monkeypatch.setattr(ingest, "ARCHIVE_ONLY_MAX", 1024)
    f = tmp_path / "blob.bin"
    f.write_bytes(os.urandom(4096))
    r = ingest.ingest_file(str(f))
    assert not r["ok"] and "过大" in r["error"]


def test_tag_circuit_breaker(monkeypatch):
    import openai

    from pda import config, llm

    monkeypatch.setattr(llm, "has_llm", lambda: True)
    monkeypatch.setattr(config, "auto_tags_enabled", lambda: True)
    monkeypatch.setattr(llm, "_tags_offline_until", 0.0)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise openai.APIConnectionError(request=None)

    monkeypatch.setattr(llm, "chat", boom)
    assert llm.generate_tags("文本") == []
    assert llm.generate_tags("文本") == []
    assert len(calls) == 1  # 第二次被熔断，不再请求


def test_short_terms():
    from pda import db

    assert db._short_terms("怎么报销") == ["报销"]
    assert db._short_terms("合同的付款条款是什么") == ["合同"]
    assert db._short_terms("发布会定在") == []


def test_fts_matches_two_char_words(store, tmp_path):
    from pda import db, ingest

    f = tmp_path / "流程.txt"
    f.write_text("员工出差返回后提交报销单，附发票原件。", encoding="utf-8")
    ingest.ingest_file(str(f))
    hits = db.fts_search("怎么报销")
    assert hits and "报销" in hits[0]["text"]


def test_retrieve_drops_far_vectors_and_fuses(monkeypatch):
    from pda import db, qa, vectorstore

    vec = [
        {"chunk_id": 1, "text": "a", "distance": 0.3},
        {"chunk_id": 2, "text": "b", "distance": 0.4},
        {"chunk_id": 3, "text": "c", "distance": 0.9},  # 太远，丢弃
    ]
    fts = [{"id": 2, "text": "b"}, {"id": 4, "text": "d"}]
    monkeypatch.setattr(vectorstore, "search", lambda *a, **k: vec)
    monkeypatch.setattr(db, "fts_search", lambda *a, **k: fts)
    ids = [h.get("chunk_id") or h.get("id") for h in qa.retrieve("q", query_vec=[0.0])]
    assert ids == [2, 1, 4]  # 两路都命中的 2 排第一


def test_answer_unrelated_question_returns_nothing(monkeypatch):
    from pda import db, qa, vectorstore

    monkeypatch.setattr(qa.embeddings, "embed", lambda t: [[0.0]])
    monkeypatch.setattr(vectorstore, "search",
                        lambda *a, **k: [{"chunk_id": 1, "text": "x", "distance": 0.8}])
    monkeypatch.setattr(db, "fts_search", lambda *a, **k: [])
    assert qa.answer("今天天气怎么样")["sources"] == []


def test_read_xls(tmp_path):
    xlwt = pytest.importorskip("xlwt")  # 只用来生成测试文件，不是运行依赖
    import datetime

    from pda import parsers

    wb = xlwt.Workbook()
    ws = wb.add_sheet("销售")
    for c, v in enumerate(["地区", "销售额", "日期"]):
        ws.write(0, c, v)
    ws.write(1, 0, "华东")
    ws.write(1, 1, 1200)
    ws.write(1, 2, datetime.datetime(2025, 9, 30), xlwt.easyxf(num_format_str="YYYY-MM-DD"))
    f = tmp_path / "老表格.xls"
    wb.save(str(f))
    text = parsers.extract_text(str(f))
    assert "销售 | 地区 | 销售额 | 日期" in text
    assert "销售 | 华东 | 1200 | 2025-09-30" in text


def test_read_xls_rejects_fake(tmp_path):
    from pda import parsers

    f = tmp_path / "假的.xls"
    f.write_bytes(b"not an excel file")
    with pytest.raises(parsers.ParseError):
        parsers.extract_text(str(f))
