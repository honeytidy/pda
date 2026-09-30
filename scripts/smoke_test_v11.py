# -*- coding: utf-8 -*-
"""V1.1 冒烟测试（不依赖主窗口 GUI）。

覆盖：
1. 新格式入库：xlsx / pptx / 图片 OCR（OCR 不可用时验证优雅降级）
2. 混合检索能命中新格式内容
3. 重复收录行为：未变化跳过、变更后覆盖更新（旧 chunks 删除）
4. watcher 防抖收集与定时扫描降级（直接调内部函数，不起真实监控）
5. 剪贴板笔记保存函数

用法：python scripts/smoke_test_v11.py（不弹窗，无需 offscreen）
使用独立临时数据目录，结束后自动清理。
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP_DIR = Path(tempfile.mkdtemp(prefix="pda_smoke_v11_"))
os.environ["PDA_DATA_DIR"] = str(TMP_DIR / "data")
os.environ["PDA_CONFIG_PATH"] = str(TMP_DIR / "nonexistent_config.json")
os.environ.pop("PDA_API_KEY", None)
# 注意：不设 QT_QPA_PLATFORM=offscreen——offscreen 下字体渲染退化会导致 OCR 测试图无文字。
# 本脚本不弹出任何窗口，使用默认 windows 平台即可。

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402

from pda import db, ingest, parsers, qa  # noqa: E402
from pda.watcher import FolderWatcher  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f"：{detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def make_samples(work: Path) -> dict:
    # xlsx
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "销售数据"
    ws.append(["区域", "季度", "销售额"])
    ws.append(["华东", "Q3", "120万元"])
    ws.append(["华北", "Q3", "95万元"])
    xlsx = work / "销售数据表.xlsx"
    wb.save(str(xlsx))

    # pptx
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "产品计划"
    box = slide.shapes.add_textbox(100000, 100000, 4000000, 1000000)
    box.text_frame.text = "新品发布会定在 11 月 15 日，地点会展中心"
    pptx = work / "产品计划.pptx"
    prs.save(str(pptx))

    # png（QImage 绘制文字）
    img = QImage(640, 160, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setPen(QColor("black"))
    p.setFont(QFont("Microsoft YaHei UI", 28))
    p.drawText(30, 95, "优惠券代码 SAVE2026")
    p.end()
    png = work / "优惠券.png"
    img.save(str(png))

    return {"xlsx": str(xlsx), "pptx": str(pptx), "png": str(png)}


def retrieval_hits(query: str, keyword: str) -> tuple:
    hits = qa.retrieve(query)
    joined = "\n".join(h.get("text", "") for h in hits)
    titles = [h.get("title", "") for h in hits]
    return keyword in joined, titles


def expire_debounce(watcher):
    """把所有待处理文件的"最后事件时间"改到防抖期之前。"""
    with watcher._lock:
        for entry in watcher._pending.values():
            entry[0] = 0


def main():
    app = QGuiApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    print(f"临时数据目录：{TMP_DIR}")
    work = TMP_DIR / "samples"
    work.mkdir(parents=True)
    files = make_samples(work)

    print("\n=== 1. 新格式入库 ===")
    db.init_db()
    ocr_ok = parsers.ocr_available()
    print(f"  OCR 组件可用：{ocr_ok}")
    results = {k: ingest.ingest_file(v) for k, v in files.items()}
    for k, r in results.items():
        print(f"  {k}: {r}")
    check("xlsx 入库", results["xlsx"]["ok"])
    check("pptx 入库", results["pptx"]["ok"])
    if ocr_ok:
        check("png OCR 入库", results["png"]["ok"],
              results["png"].get("error", ""))
    else:
        check("png 优雅降级", not results["png"]["ok"]
              and "OCR 组件不可用" in results["png"]["error"],
              results["png"].get("error", ""))

    print("\n=== 2. 检索命中新格式 ===")
    hit, titles = retrieval_hits("华东的销售额是多少", "120万元")
    check("xlsx 内容可检索", hit, str(titles))
    hit, titles = retrieval_hits("发布会什么时候", "11 月 15 日")
    check("pptx 内容可检索", hit, str(titles))
    if ocr_ok:
        hit, titles = retrieval_hits("优惠券代码是什么", "SAVE2026")
        check("png OCR 内容可检索", hit, str(titles))

    print("\n=== 3. 重复收录行为 ===")
    r = ingest.ingest_file(files["xlsx"])
    check("未变化重复收录被跳过", r["ok"] and r.get("skipped"), str(r))
    doc_count_before = len(db.list_recent_documents(100))
    # 修改文件（追加一行）
    from openpyxl import load_workbook

    wb = load_workbook(files["xlsx"])
    wb["销售数据"].append(["华南", "Q4", "180万元"])
    wb.save(files["xlsx"])
    wb.close()
    time.sleep(0.05)  # 确保 mtime 变化
    r = ingest.ingest_file(files["xlsx"])
    check("变更后覆盖更新", r["ok"] and r.get("replaced"), str(r))
    doc_count_after = len(db.list_recent_documents(100))
    check("文档总数不增（旧记录已删）", doc_count_after == doc_count_before,
          f"{doc_count_before} -> {doc_count_after}")
    hit, _ = retrieval_hits("华南 Q4 销售额", "180万元")
    check("更新后的内容可检索", hit)
    hit, _ = retrieval_hits("华东的销售额是多少", "120万元")
    check("原有内容仍可检索（无旧索引残留干扰）", hit)

    print("\n=== 4. watcher 防抖与降级扫描 ===")
    got = []
    watcher = FolderWatcher()
    watcher.files_ready.connect(got.extend)
    new_file = work / "销售数据表.xlsx"  # 已存在的支持文件
    watcher._on_fs_event(str(new_file))
    watcher._flush_pending()  # 未到 2 秒防抖期，不应发射
    check("防抖期内不发射", not got)
    expire_debounce(watcher)  # 模拟已超过防抖期：第一次检查只记录文件签名
    watcher._flush_pending()
    check("首次到期先确认写入稳定", not got, str(got))
    expire_debounce(watcher)
    watcher._flush_pending()  # 签名未变：判定写入稳定
    check("防抖到期发射文件", got == [str(new_file)], str(got))

    # 临时/锁定文件不收
    from pda.watcher import is_candidate
    for name in ("~$报告.docx", "~WRL0001.tmp", "a.pdf.crdownload", ".hidden.md", "x.exe"):
        check(f"忽略 {name}", not is_candidate(str(work / name)))
    check("正常文件收录", is_candidate(str(work / "报告.docx")))

    # 降级：定时扫描快照对比
    got2 = []
    watch_dir = TMP_DIR / "watched"
    watch_dir.mkdir()
    watcher2 = FolderWatcher()
    watcher2.files_ready.connect(got2.extend)
    watcher2.start([str(watch_dir)])
    check("降级后端启动", watcher2.backend_name() in ("watchdog", "定时扫描"),
          watcher2.backend_name())
    (watch_dir / "新文档.txt").write_text("监控文件夹自动收录测试：关键词紫罗兰", encoding="utf-8")
    watcher2._poll_once()  # 手动触发一次扫描（等价于 10 秒定时器）
    import time as _t
    for _ in range(50):  # 轮询在后台线程
        if watcher2._pending:
            break
        _t.sleep(0.1)
    for _ in range(2):
        expire_debounce(watcher2)
        watcher2._flush_pending()
    check("扫描发现新文件", any("新文档.txt" in p for p in got2), str(got2))
    watcher2.stop()
    watcher.stop()

    print("\n=== 5. 剪贴板笔记保存 ===")
    note_path = ingest.save_clipboard_note("这是一条剪贴板笔记：项目代号蓝宝石")
    check("笔记文件已写入 notes 目录",
          "notes" in note_path and os.path.isfile(note_path), note_path)
    r = ingest.ingest_file(note_path)
    check("笔记入库", r["ok"])
    hit, _ = retrieval_hits("项目代号是什么", "蓝宝石")
    check("笔记内容可检索", hit)

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n临时目录已清理。总体结果：{'全部通过' if not FAILURES else f'失败 {len(FAILURES)} 项: {FAILURES}'}")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    try:
        main()
    finally:
        # 中途异常也要清理临时目录（chroma 仍占用文件时 ignore_errors 兜底）
        shutil.rmtree(TMP_DIR, ignore_errors=True)
