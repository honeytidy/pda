# -*- coding: utf-8 -*-
"""应用图标：accent 圆角方块底 + 白色 θ 字符旋转 90°（眼睛造型）。

用 Segoe UI Symbol 的 θ 字形渲染后旋转 90°——笔画均匀、椭圆端正，最方正。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

ACCENT = "#3B6EF6"


def make_pixmap(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # accent 圆角方块底
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    # θ（Times New Roman 粗体）旋转 90°，居中
    font = QFont("Segoe UI Symbol")
    font.setBold(True)
    font.setPixelSize(int(size * 0.72))
    p.setFont(font)
    p.setPen(QColor("white"))
    p.translate(size / 2, size / 2)
    p.rotate(90)
    p.drawText(
        QRectF(-size / 2, -size / 2, size, size), Qt.AlignHCenter | Qt.AlignVCenter, "θ"
    )
    p.end()
    return pm


def make_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        icon.addPixmap(make_pixmap(s))
    return icon
