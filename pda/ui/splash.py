# -*- coding: utf-8 -*-
"""启动 splash：无边框圆角卡片，屏幕居中，分阶段状态文字。

设计要点：模块只依赖 PySide6（不 import pda 后端），保证 run.py 能在
加载重依赖（chromadb/fastembed 等）之前尽早显示。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

ACCENT = "#3B6EF6"
BORDER = "#E5E7EB"
TEXT = "#1F2329"
SUBTLE = "#8F959E"

SPLASH_QSS = f"""
QFrame#splashCard {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#splashBrand {{
    background: transparent;
}}
QLabel#splashBrandText {{
    color: #FFFFFF;
    font-size: 24px;
    font-weight: 600;
    background: transparent;
}}
QLabel#splashTitle {{
    color: {TEXT};
    font-size: 20px;
    font-weight: 600;
    background: transparent;
}}
QLabel#splashSubtitle {{
    color: {SUBTLE};
    font-size: 13px;
    background: transparent;
}}
QLabel#splashStatus {{
    color: {SUBTLE};
    font-size: 12px;
    background: transparent;
}}
"""


class SplashScreen(QWidget):
    """启动画面。show_status() 更新状态并立即重绘；finish() 在主窗口出现后关闭。"""

    def __init__(self):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(SPLASH_QSS)
        self.setFixedSize(420, 240)

        card = QFrame(self)
        card.setObjectName("splashCard")
        card.setGeometry(0, 0, 420, 240)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(6)

        brand = QFrame()
        brand.setObjectName("splashBrand")
        brand.setFixedSize(56, 56)
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        from .icon import make_pixmap

        brand_text = QLabel()
        brand_text.setPixmap(make_pixmap(56))  # 眼睛 logo（θ 横放）
        brand_text.setAlignment(Qt.AlignCenter)
        brand_layout.addWidget(brand_text)
        layout.addWidget(brand, 0, Qt.AlignHCenter)

        title = QLabel("个人助理知识库")
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("你的本地 AI 知识库")
        subtitle.setObjectName("splashSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addStretch(1)

        self._status = QLabel("正在初始化…")
        self._status.setObjectName("splashStatus")
        self._status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status)

        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center() - self.rect().center())

    def show_status(self, text: str):
        self._status.setText(text)
        app = QGuiApplication.instance()
        if app is not None:
            app.processEvents()

    def finish(self, window: QWidget):
        self.close()
