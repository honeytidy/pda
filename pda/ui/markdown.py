# -*- coding: utf-8 -*-
"""把 LLM 回答的 Markdown 渲染成 QLabel 可显示的富文本 HTML。

用 Qt 自带的 Markdown 解析（QTextDocument.setMarkdown，GitHub 方言），不引入新依赖。
解析后在 QTextDocument 上直接调格式（标题字号、代码底色、表格边框、段落间距），
再 toHtml() 交给 QLabel —— QLabel 的富文本引擎就是 QTextDocument，格式能原样还原。

安全：
- MarkdownNoHTML：模型输出里的原始 HTML 当普通文本显示，不会被解释
- 图片一律替换为"[图片]"文字：否则 ![](file:///...) 会让 QLabel 去读本地文件
- 只保留 http/https 链接，其余（file:、javascript: 等）去掉超链接只留文字
"""
import re

from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextTable,
)

CODE_BG = "#F3F4F6"
QUOTE_COLOR = "#646A73"
TABLE_BORDER = "#D0D3D9"
TABLE_HEADER_BG = "#F5F6F8"
CODE_FONTS = ["Consolas", "Microsoft YaHei UI"]  # 与 theme.MONO_FAMILIES 一致

# 标题字号（px），正文 14px
_HEADING_PX = {1: 19, 2: 17, 3: 15}
_LINK_SCHEMES = ("http://", "https://")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_TABLE_ROW = re.compile(r"^\s*\|")


def _hard_breaks(text: str) -> str:
    """单个换行保留为换行。

    CommonMark 把段落内的单换行当空格（中文会变成"上一行 下一行"挤在一起），
    而模型常用单换行分行。代码块、表格行不处理（表格行末加空格不影响解析，但没必要）。
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (not in_fence and line.strip() and nxt.strip()
                and not _TABLE_ROW.match(line) and not _TABLE_ROW.match(nxt)
                and not _FENCE.match(nxt)):
            line = line.rstrip() + "  "
        out.append(line)
    return "\n".join(out)


def _style_tables(frame):
    for child in frame.childFrames():
        if isinstance(child, QTextTable):
            fmt = child.format()
            fmt.setBorder(1)
            fmt.setBorderBrush(QColor(TABLE_BORDER))
            fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
            fmt.setBorderCollapse(True)
            fmt.setCellSpacing(0)
            fmt.setCellPadding(6)
            fmt.setTopMargin(4)
            fmt.setBottomMargin(8)
            child.setFormat(fmt)
            for col in range(child.columns()):
                cell = child.cellAt(0, col)
                cf = cell.format().toTableCellFormat()
                cf.setBackground(QColor(TABLE_HEADER_BG))
                cell.setFormat(cf)
        _style_tables(child)


def render(text: str, font_px: int = 14, link_color: str = "#3B6EF6") -> tuple:
    """返回 (html, natural_width)。natural_width 为不换行时的内容宽度（px），供气泡定宽。"""
    doc = QTextDocument()
    font = QFont()
    font.setPixelSize(font_px)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    doc.setIndentWidth(20)  # 列表每级缩进（默认 40px，嵌套两级就挤到气泡中间）
    doc.setMarkdown(
        _hard_breaks(text),
        QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML,
    )

    cursor = QTextCursor(doc)
    images = []
    block = doc.begin()
    while block.isValid():
        bf = block.blockFormat()
        level = bf.headingLevel()
        is_code = bf.hasProperty(QTextFormat.BlockCodeLanguage)
        is_quote = bf.hasProperty(QTextFormat.BlockQuoteLevel)
        in_table = QTextCursor(block).currentTable() is not None

        # ---- 块格式：间距、代码底色、引用缩进 ----
        nbf = QTextBlockFormat(bf)
        if level:
            nbf.setTopMargin(8)
            nbf.setBottomMargin(4)
        elif is_code:
            nbf.setBackground(QColor(CODE_BG))
            nbf.setLeftMargin(0)
            nbf.setTopMargin(0)
            # 代码块最后一行留出与下一段的间距（中间行保持 0，底色连成一片）
            nxt = block.next()
            last = not (nxt.isValid()
                        and nxt.blockFormat().hasProperty(QTextFormat.BlockCodeLanguage))
            nbf.setBottomMargin(6 if last else 0)
        elif is_quote:
            nbf.setLeftMargin(12)
            nbf.setRightMargin(0)
            nbf.setBottomMargin(6)
        elif block.textList() is not None:
            nbf.setBottomMargin(2)
        elif not in_table:
            nbf.setBottomMargin(6)
        cursor.setPosition(block.position())
        cursor.setBlockFormat(nbf)

        # ---- 字符格式：先收集，再统一改（改格式不改长度，片段位置不变） ----
        edits = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                cf = frag.charFormat()
                if cf.isImageFormat():
                    images.append((frag.position(), frag.length()))
                else:
                    edits.append((frag.position(), frag.length(), cf))
            it += 1
        for pos, length, cf in edits:
            ncf = QTextCharFormat(cf)
            if level:
                ncf.clearProperty(QTextFormat.FontSizeAdjustment)
                ncf.setFontWeight(QFont.DemiBold)
                f = ncf.font()
                f.setPixelSize(_HEADING_PX.get(level, font_px))
                ncf.setFont(f, QTextCharFormat.FontPropertiesSpecifiedOnly)
            if is_code or cf.fontFixedPitch():
                ncf.setFontFamilies(CODE_FONTS)
                ncf.setFontFixedPitch(False)
                f = ncf.font()
                f.setPixelSize(font_px - 1)
                ncf.setFont(f, QTextCharFormat.FontPropertiesSpecifiedOnly)
                if not is_code:
                    ncf.setBackground(QColor(CODE_BG))  # 行内代码
            if is_quote:
                ncf.setForeground(QColor(QUOTE_COLOR))
            if cf.isAnchor():
                href = cf.anchorHref()
                if href.lower().startswith(_LINK_SCHEMES):
                    ncf.setForeground(QColor(link_color))
                    ncf.setFontUnderline(False)
                else:
                    ncf.setAnchor(False)
                    ncf.clearProperty(QTextFormat.AnchorHref)
                    ncf.clearForeground()
                    ncf.setFontUnderline(False)
            cursor.setPosition(pos)
            cursor.setPosition(pos + length, QTextCursor.KeepAnchor)
            cursor.setCharFormat(ncf)
        block = block.next()

    # 图片换成文字：倒序替换，前面的位置不受影响
    for pos, length in sorted(images, reverse=True):
        cursor.setPosition(pos)
        cursor.setPosition(pos + length, QTextCursor.KeepAnchor)
        cursor.insertText("[图片]", QTextCharFormat())

    _style_tables(doc.rootFrame())
    doc.setTextWidth(-1)
    return doc.toHtml(), doc.idealWidth()
