# -*- coding: utf-8 -*-
"""极简 toast 通知：屏幕中央深色浮条，淡出关闭，多条向下堆叠。

三种状态：
- 进度态（pending=True）：图标 "…"，30 秒兜底超时（防卡死残留），
  通过 update() 就地切换为结果态
- 成功 / 失败：图标 ✓ 蓝 / ✘ 红，2.5 秒后淡出

非模态纯展示：FramelessWindowHint | Tool | WindowStaysOnTopHint +
WA_ShowWithoutActivating（不抢焦点、不出现在任务栏），绝不阻塞主线程。
"""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

TOAST_QSS = """
QFrame#toastFrame {
    background: #323232;
    border-radius: 8px;
}
QLabel#toastIcon {
    font-size: 14px;
    background: transparent;
}
QLabel#toastText {
    color: #FFFFFF;
    font-size: 13px;
    background: transparent;
}
"""

ACCENT = "#3B6EF6"
FAIL_COLOR = "#F5483B"
PENDING_COLOR = "#C9CDD4"


class Toast(QWidget):
    _active: list = []

    HEIGHT = 38
    MAX_WIDTH = 420
    MARGIN = 16
    GAP = 8
    DISPLAY_MS = 2500
    PENDING_MS = 30_000  # 进度态兜底超时
    FADE_MS = 300

    def __init__(self, text: str, success: bool = True, pending: bool = False):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(TOAST_QSS)

        frame = QFrame(self)
        frame.setObjectName("toastFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 0, 14, 0)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setObjectName("toastIcon")
        self._icon.setFixedWidth(18)
        layout.addWidget(self._icon)

        font = self.font()
        font.setPixelSize(13)
        self._fm = QFontMetrics(font)
        self._label = QLabel()
        self._label.setObjectName("toastText")
        self._label.setFont(font)
        layout.addWidget(self._label)

        self._frame = frame
        self._fade = None
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._start_fade)

        Toast._active.append(self)
        self._render(text, success, pending)

    # ---------- 内容与定位 ----------

    def _render(self, text: str, success: bool, pending: bool):
        """更新文字/图标/宽度/位置/计时（构造与 update 共用）。"""
        # 淡出动画已启动则停掉并恢复不透明
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
            self.setWindowOpacity(1.0)

        self._pending = pending
        if pending:
            self._icon.setText("…")
            color = PENDING_COLOR
        else:
            self._icon.setText("✓" if success else "✘")
            color = ACCENT if success else FAIL_COLOR
        self._icon.setStyleSheet(f"color: {color};")

        max_text_w = self.MAX_WIDTH - 12 - 14 - 18 - 8
        text_w = self._fm.horizontalAdvance(text)
        self._label.setText(self._fm.elidedText(text, Qt.ElideRight, max_text_w))

        width = min(self.MAX_WIDTH, 12 + 14 + 18 + 8 + text_w + 4)
        self.setFixedSize(width, self.HEIGHT)
        self._frame.setGeometry(0, 0, width, self.HEIGHT)

        # 屏幕中央堆叠：水平居中（宽度变化时中心不动），第一条垂直居中，后续向下排
        geo = QGuiApplication.primaryScreen().availableGeometry()
        index = Toast._active.index(self) if self in Toast._active else 0
        x = geo.left() + (geo.width() - width) // 2
        y = (
            geo.top()
            + (geo.height() - self.HEIGHT) // 2
            + index * (self.HEIGHT + self.GAP)
        )
        self.move(x, y)

        # 重启消失计时：进度态 30s 兜底，结果态 2.5s
        self._close_timer.stop()
        self._close_timer.start(self.PENDING_MS if pending else self.DISPLAY_MS)

    def update(self, text: str, success: bool = True, pending: bool = False):
        """就地更新：换文案/图标，宽度自适应并重新居中，重启消失计时。"""
        self._render(text, success, pending)

    def is_alive(self) -> bool:
        return self in Toast._active

    # ---------- 消失 ----------

    def _start_fade(self):
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade.finished.connect(self.close)
        self._fade.start()

    def closeEvent(self, event):
        if self._close_timer is not None:
            self._close_timer.stop()
        if self in Toast._active:
            Toast._active.remove(self)
        super().closeEvent(event)


def show_toast(text: str, success: bool = True) -> Toast:
    """显示一条结果 toast（2.5s 淡出），返回实例。"""
    toast = Toast(text, success)
    toast.show()
    return toast


def show_progress(text: str) -> Toast:
    """显示一条进度 toast（30s 兜底），返回实例供 update() 就地更新。"""
    toast = Toast(text, pending=True)
    toast.show()
    return toast
