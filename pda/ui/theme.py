# -*- coding: utf-8 -*-
"""界面字体与字号规范（全应用统一，splash/toast/主窗口/预览脚本共用）。

字体：Windows 11 系统界面的标准搭配——西文/数字用 Segoe UI，中文回落到微软雅黑 UI。
只写一个"微软雅黑"时，数字和英文也用雅黑的西文字形，偏宽、发虚，观感不正式；
Qt 按 families 顺序逐字回落，同一行里中英文各用各的字体。

字号（px）：只用这几档，别在代码里随手写别的数字。
"""
from PySide6.QtGui import QFont

FONT_FAMILIES = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
MONO_FAMILIES = ["Consolas", "Microsoft YaHei UI"]

TITLE_PX = 20     # 欢迎标题、对话框大标题
HEADING_PX = 15   # 卡片/分组标题
BODY_PX = 14      # 正文：聊天气泡、输入框、文档标题
SECONDARY_PX = 13 # 次要正文：设置项、按钮、出处
CAPTION_PX = 12   # 说明文字、元信息、状态栏

SEMIBOLD = 600    # 标题字重（Segoe UI Semibold；中文按雅黑粗体显示）


def app_font() -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(BODY_PX)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferNoHinting)  # 分数缩放下字形更匀称
    return font
