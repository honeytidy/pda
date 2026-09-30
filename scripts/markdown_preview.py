# -*- coding: utf-8 -*-
"""Markdown 渲染预览：主窗口里放几条典型 LLM 回答，截图到 scripts/markdown_preview.png。

覆盖标题、粗斜体、有序/无序/嵌套列表、表格、行内代码、代码块、引用、链接，
以及应被屏蔽的原始 HTML、本地图片、file: 链接；最后一条是纯文本兜底（不应渲染）。

用法：python scripts/markdown_preview.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["PDA_DATA_DIR"] = tempfile.mkdtemp(prefix="pda_preview_")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pda.ui.theme import app_font  # noqa: E402
from pda.ui.main_window import MainWindow  # noqa: E402

OUT = Path(__file__).resolve().parent / "markdown_preview.png"

SRC = [{
    "index": 1, "title": "一般项目可行性报告.docx",
    "file_path": "D:/demo/files/一般项目可行性报告.docx",
    "snippet": "项目总投资估算为 1200 万元，其中建设投资 950 万元…",
}]

ANSWER_1 = """## 投资估算
根据可行性报告，项目**总投资 1200 万元** [1]，构成如下：

| 项目 | 金额（万元） | 占比 |
|---|---:|---:|
| 建设投资 | 950 | 79% |
| 流动资金 | 180 | 15% |
| 预备费 | 70 | 6% |

### 主要风险
1. 原材料价格波动，*影响毛利*
2. 审批周期较长
   - 环评预计 3 个月
   - 规划许可预计 2 个月

> 报告建议分两期建设，以降低一次性投入压力 [1]。"""

ANSWER_2 = """可以用 `pip install -r requirements.txt` 安装依赖，然后运行：

```python
from pda import qa
print(qa.answer("项目总投资是多少")["answer"])
```

详细说明见 [官方文档](https://docs.python.org/3/)。
下面这些不应生效：<b>原始HTML</b>、![本地图片](file:///C:/Windows/win.ini)、[本地链接](file:///C:/Windows)"""

FALLBACK = ("未配置 API Key，无法生成智能回答。以下是检索到的相关资料片段：\n\n"
            "[1] 一般项目可行性报告.docx：**注意** 这是原文片段 # 不是标题 * 也不是列表")


def main():
    app = QApplication(sys.argv)
    app.setFont(app_font())
    win = MainWindow()
    win.setAttribute(Qt.WA_DontShowOnScreen)
    win.resize(1100, 1500)
    win._append_user("项目总投资是多少？有哪些风险？")
    win._add_assistant(ANSWER_1, SRC, is_markdown=True)
    win._append_user("怎么在代码里调用问答？")
    win._add_assistant(ANSWER_2, [], is_markdown=True)
    win._append_user("没配置 key 会怎样？")
    win._add_assistant(FALLBACK, SRC)
    win.show()
    app.processEvents()
    # 滚到顶部，截整窗
    win.scroll.verticalScrollBar().setValue(0)
    app.processEvents()
    win.grab().save(str(OUT))
    win._quit_app()
    app.processEvents()
    print(f"saved: {OUT}")


if __name__ == "__main__":
    main()
