# -*- coding: utf-8 -*-
"""UI 预览：生成两张场景截图。

- scripts/ui_preview_empty.png：空状态（首次启动，欢迎面板 + 空侧栏）
- scripts/ui_preview.png：满消息状态（11 条混合消息，欢迎面板应已隐藏）

用 WA_DontShowOnScreen 离屏渲染（不弹窗、不使用 offscreen 平台，保证中文字体正常）。

用法：python scripts/ui_preview.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["PDA_DATA_DIR"] = tempfile.mkdtemp(prefix="pda_preview_")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

from pda.ui.theme import app_font  # noqa: E402
from pda.ui.main_window import DocCard, MainWindow  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent


def _new_window(app):
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen)
    win.resize(1100, 720)
    win.statusBar().showMessage(
        "就绪（拖入文件或文件夹即可收录；Ctrl+Shift+Q 保存剪贴板）"
    )
    return win


def _grab(win, app, name):
    win.show()
    app.processEvents()
    out = OUT_DIR / name
    win.grab().save(str(out))
    win._quit_app()  # 托盘模式下 close() 只是最小化，退出要走 _quit_app 清理线程
    app.processEvents()
    print(f"saved: {out}")


def preview_empty(app):
    win = _new_window(app)
    assert win._welcome_panel is not None, "空状态下欢迎面板应存在"
    _grab(win, app, "ui_preview_empty.png")


def preview_full(app):
    win = _new_window(app)

    win._add_system_notice(
        "收录完成 · 张三的季度报告.txt：3 个片段；会议纪要.docx：2 个片段"
    )
    win._append_user("我们 Q3 的销售额是多少？帮我总结一下季度报告的要点。")
    win._add_assistant(
        "根据知识库中的资料，Q3 销售额达到 120 万元，同比增长 15% [1]。"
        "主要增长来自华东区域的新客户拓展。\n\n"
        "季度报告要点：\n1. 销售额 120 万元，同比增长 15% [1]\n"
        "2. 华东区域新客户是主要增长点 [1]\n3. Q4 目标冲刺 150 万元 [1]",
        [
            {
                "index": 1,
                "title": "张三的季度报告.txt",
                "file_path": "D:/demo/files/张三的季度报告.txt",
                "snippet": "Q3 季度销售业绩总结：本季度销售额达到 120 万元，同比增长 15%…",
            },
            {
                "index": 2,
                "title": "会议纪要.docx",
                "file_path": "D:/demo/files/会议纪要.docx",
                "snippet": "决议：产品发布会定在 11 月 15 日，地点为公司大会议室…",
            },
        ],
    )
    win._append_user("发布会定在什么时候？")
    win._add_assistant(
        "产品发布会定在 11 月 15 日，地点为公司大会议室 [1]。"
        "会后行动项：市场部负责邀请函，研发部冻结功能 [1]。",
        [
            {
                "index": 1,
                "title": "会议纪要.docx",
                "file_path": "D:/demo/files/会议纪要.docx",
                "snippet": "决议：产品发布会定在 11 月 15 日，地点为公司大会议室…",
            },
        ],
    )
    win._append_user("华东区域的增长主要做了什么动作？有没有渠道侧的具体打法，"
                     "以及对应的负责人和预算安排？")
    win._add_assistant(
        "资料中只提到“主要增长来自华东区域的新客户拓展”[1]，"
        "没有展开渠道打法、负责人或预算的细节，建议补充区域周报后再问。\n\n"
        "另外从产品规划文档看，Q4 会在华东试点渠道伙伴计划 [2]，"
        "包括联合拜访、返点激励与月度复盘三项机制，预算暂定 30 万元，"
        "由区域总监统一审批，具体名单预计在 10 月底前敲定并同步给市场部。",
        [
            {
                "index": 1,
                "title": "张三的季度报告.txt",
                "file_path": "D:/demo/files/张三的季度报告.txt",
                "snippet": "主要增长来自华东区域的新客户拓展…",
            },
            {
                "index": 2,
                "title": "产品规划 2026.md",
                "file_path": "D:/demo/files/产品规划 2026.md",
                "snippet": "Q4 在华东试点渠道伙伴计划，含联合拜访、返点激励…",
            },
        ],
    )
    win._add_system_notice("收录完成 · 产品规划 2026.md：8 个片段")
    win._append_user("Q4 目标是多少？")
    win._add_assistant("Q4 销售额目标为 150 万元，重点推进大客户续约 [1]。", [])

    assert win._welcome_panel is None, "有消息后欢迎面板应已隐藏"

    # 侧栏样例文档
    samples = [
        ("张三的季度报告.txt", "09-24 14:30 · 3 个片段"),
        ("会议纪要.docx", "09-24 14:31 · 2 个片段"),
        ("产品规划 2026.md", "09-24 15:02 · 8 个片段"),
    ]
    win.empty_hint.hide()
    win.doc_list.show()
    for title, meta in samples:
        item = QListWidgetItem()
        card = DocCard(title, meta)
        item.setSizeHint(card.sizeHint())
        win.doc_list.addItem(item)
        win.doc_list.setItemWidget(item, card)

    _grab(win, app, "ui_preview.png")


def main():
    app = QApplication(sys.argv)
    app.setFont(app_font())
    preview_empty(app)
    preview_full(app)


if __name__ == "__main__":
    main()
