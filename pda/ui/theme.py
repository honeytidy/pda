# -*- coding: utf-8 -*-
"""界面字体与字号规范（全应用统一，splash/toast/主窗口/预览脚本共用）。

字体：公文/文档的标准搭配——西文/数字用 Times New Roman，中文回落到宋体。
Times New Roman 没有中文字形，Qt 按 families 顺序逐字回落，同一行里中英文各用各的字体。

字号（px）：只用这几档，别在代码里随手写别的数字。宋体 12px 以下笔画会糊，最小 12px。
"""
from PySide6.QtGui import QFont

FONT_FAMILIES = ["Times New Roman", "SimSun"]
MONO_FAMILIES = ["Consolas", "SimSun"]

TITLE_PX = 19     # 欢迎标题、对话框大标题
HEADING_PX = 14   # 卡片/分组标题
BODY_PX = 13      # 正文：聊天气泡、输入框、文档标题
SECONDARY_PX = 12 # 次要正文：设置项、按钮、出处
CAPTION_PX = 12   # 说明文字、元信息、状态栏

SEMIBOLD = 600    # 标题字重（宋体无粗体字形，由系统加粗合成）


def app_font() -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(BODY_PX)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferNoHinting)  # 分数缩放下字形更匀称
    return font
