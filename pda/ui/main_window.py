# -*- coding: utf-8 -*-
"""PySide6 主窗口：现代聊天应用风格。

布局：左侧聊天气泡列表 + 底部输入卡片，右侧"最近收录"侧栏；
全窗口拖放入库（带半透明遮罩反馈）；入库/问答均在 QThread 工作线程执行。
"""
import os
import time

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, embeddings, hotkey, ingest, ipc, llm, qa, web
from ..watcher import FolderWatcher
from . import markdown, theme
from .toast import show_progress, show_toast

ACCENT = "#3B6EF6"
ACCENT_DARK = "#2F5BDB"
BG = "#F4F5F7"
BORDER = "#E5E7EB"
TEXT = "#1F2329"
SUBTLE = "#8F959E"

QSS = f"""
QMainWindow, QWidget#centralRoot {{
    background: {BG};
}}
QSplitter::handle {{
    background: {BORDER};
}}
/* ---------- 聊天滚动区 ---------- */
QScrollArea#chatScroll {{
    background: transparent;
    border: none;
}}
QWidget#messagesContainer {{
    background: transparent;
}}
/* ---------- 细滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #D4D7DC;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #B8BDC4;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
/* ---------- 聊天气泡 ---------- */
QFrame#userBubble {{
    background: {ACCENT};
    border-radius: 10px;
}}
QFrame#userBubble QLabel {{
    color: #FFFFFF;
    font-size: 14px;
    background: transparent;
}}
QFrame#assistantBubble {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#assistantBubble QLabel {{
    color: {TEXT};
    font-size: 14px;
    background: transparent;
}}
QFrame#assistantBubble QLabel.sourceLink {{
    color: {ACCENT};
    font-size: 13px;
}}
QLabel#systemNotice {{
    color: {SUBTLE};
    font-size: 12px;
    background: #E9EBEF;
    border-radius: 10px;
    padding: 4px 12px;
}}
/* ---------- 空状态欢迎面板 ---------- */
QWidget#welcomePanel {{
    background: transparent;
}}
QLabel#welcomeTitle {{
    color: {TEXT};
    font-size: 22px;
    font-weight: 600;
    background: transparent;
}}
QLabel#welcomeSubtitle {{
    color: {SUBTLE};
    font-size: 13px;
    background: transparent;
}}
QFrame#hintCard {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#hintCard[hover="true"] {{
    background: #EEF3FE;
}}
QLabel#hintCardTitle {{
    color: {TEXT};
    font-size: 15px;
    font-weight: 600;
    background: transparent;
}}
QLabel#hintCardDesc {{
    color: {SUBTLE};
    font-size: 12px;
    background: transparent;
}}
QFrame#hotkeyStrip {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#hotkeyStripTitle {{
    color: {TEXT};
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}}
QLabel#hotkeyStripText {{
    font-size: 13px;
    background: transparent;
}}
QPushButton#linkButton {{
    background: transparent;
    border: none;
    color: {ACCENT};
    font-size: 13px;
    padding: 2px 4px;
}}
QPushButton#linkButton:hover {{
    color: {ACCENT_DARK};
    text-decoration: underline;
}}
/* ---------- 输入区 ---------- */
QFrame#inputCard {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#inputCard[focused="true"] {{
    border: 1px solid {ACCENT};
}}
QPlainTextEdit#chatInput {{
    border: none;
    background: transparent;
    font-size: 14px;
    color: {TEXT};
    padding: 4px;
}}
QPushButton#sendButton {{
    background: {ACCENT};
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    padding: 0 20px;
}}
QPushButton#sendButton:hover {{
    background: {ACCENT_DARK};
}}
QPushButton#sendButton:disabled {{
    background: #C9CDD4;
}}
/* ---------- 侧栏 ---------- */
QWidget#sidebar {{
    background: #FFFFFF;
}}
QLabel#sidebarTitle {{
    color: {SUBTLE};
    font-size: 12px;
    font-weight: 600;
}}
QListWidget#docList {{
    border: none;
    background: transparent;
}}
QListWidget#docList::item {{
    border: none;
}}
QFrame#docCard {{
    background: #FFFFFF;
    border-radius: 6px;
}}
QFrame#docCard[hover="true"] {{
    background: #EEF3FE;
}}
QLabel#docTitle {{
    color: {TEXT};
    font-size: 14px;
    background: transparent;
}}
QLabel#docMeta {{
    color: {SUBTLE};
    font-size: 12px;
    background: transparent;
}}
QLabel#emptyHint {{
    color: {SUBTLE};
    font-size: 13px;
}}
/* ---------- 拖放遮罩 ---------- */
QWidget#dropOverlay {{
    background: rgba(59, 110, 246, 30);
    border: 2px dashed {ACCENT};
    border-radius: 8px;
}}
QLabel#dropOverlayText {{
    color: {ACCENT};
    font-size: 20px;
    font-weight: 600;
    background: transparent;
}}
/* ---------- 侧栏设置按钮 ---------- */
QPushButton#settingsButton {{
    background: transparent;
    border: none;
    color: {SUBTLE};
    font-size: 12px;
    padding: 4px;
}}
QPushButton#settingsButton:hover {{
    color: {ACCENT};
}}
/* ---------- 对话框 ---------- */
QDialog {{
    background: {BG};
}}
QDialog QListWidget {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-size: 13px;
}}
QDialog QPushButton {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-size: 13px;
    color: {TEXT};
    padding: 5px 14px;
}}
QDialog QPushButton:hover {{
    background: #EEF3FE;
}}
QDialog QPushButton#primaryButton {{
    background: {ACCENT};
    border: none;
    color: #FFFFFF;
}}
QDialog QPushButton#primaryButton:hover {{
    background: {ACCENT_DARK};
}}
QDialog QLabel#dialogTip {{
    color: {TEXT};
    font-size: 13px;
}}
QDialog QLabel#dialogStatus {{
    font-size: 12px;
}}
QKeySequenceEdit QLineEdit, QDialog QLineEdit, QDialog QComboBox {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
    color: {TEXT};
    min-height: 20px;
}}
QKeySequenceEdit QLineEdit:focus, QDialog QLineEdit:focus, QDialog QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QDialog QLabel {{
    font-size: 13px;
}}
/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {BG};
    color: {SUBTLE};
    font-size: 12px;
    border: none;
    padding: 1px 8px;
    min-height: 20px;
    max-height: 24px;
}}
QStatusBar::item {{
    border: none;
}}
"""


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clipboard_files(mime) -> list:
    """剪贴板/拖放数据里的本地文件路径（资源管理器复制的文件）。"""
    if mime is None or not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]


class InputEdit(QPlainTextEdit):
    """多行输入框：回车发送，Shift+Enter 换行；向外抛出焦点变化。"""

    submitted = Signal()
    focus_changed = Signal(bool)
    # 粘贴/拖入的是文件或纯图片（截图）：交给主窗口收录，不插入输入框
    ingest_requested = Signal(object)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        # 文字照常粘贴（用户在输入问题）；文件、截图这类不可能是问题的内容直接收录
        if clipboard_files(source) or (source.hasImage() and not source.text().strip()):
            self.ingest_requested.emit(source)
            return
        super().insertFromMimeData(source)

    def focusInEvent(self, event):
        self.focus_changed.emit(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.focus_changed.emit(False)
        super().focusOutEvent(event)


class DocCard(QFrame):
    """侧栏文档卡片：标题 + 元信息，hover 淡蓝底。"""

    def __init__(self, title, meta, parent=None):
        super().__init__(parent)
        self.setObjectName("docCard")
        self.setProperty("hover", False)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("docTitle")
        meta_label = QLabel(meta)
        meta_label.setObjectName("docMeta")
        layout.addWidget(title_label)
        layout.addWidget(meta_label)

    def enterEvent(self, event):
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hover(False)
        super().leaveEvent(event)

    def _set_hover(self, on):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)


_EXAMPLE_QUESTION = "上次会议纪要里发布会定在哪天？"


class HintCard(QFrame):
    """欢迎面板的快捷卡：白底圆角。

    clickable=True：hover/焦点淡蓝底 + 手形光标，鼠标单击或 Tab 聚焦后 Enter/空格触发 clicked。
    clickable=False：纯状态展示（如"语义检索已就绪"），无 hover、不可聚焦，避免"看着能点、点了没反应"。
    """

    clicked = Signal()

    def __init__(self, title, desc, clickable=True, parent=None):
        super().__init__(parent)
        self.setObjectName("hintCard")
        self.setProperty("hover", False)
        # 宽度固定、高度可增长：Windows"文本大小"放大后描述文字换行而不被截断
        self.setFixedWidth(210)
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self._title_label = QLabel()
        self._title_label.setObjectName("hintCardTitle")
        self._desc_label = QLabel()
        self._desc_label.setObjectName("hintCardDesc")
        self._desc_label.setWordWrap(True)
        layout.addWidget(self._title_label)
        layout.addWidget(self._desc_label)
        layout.addStretch(1)
        self._clickable = False
        self.set_content(title, desc, clickable)

    def set_content(self, title, desc, clickable=True):
        self._title_label.setText(title)
        self._desc_label.setText(desc)
        self._clickable = clickable
        self.setAccessibleName(title)
        self.setAccessibleDescription(desc)
        if clickable:
            self.setCursor(QCursor(Qt.PointingHandCursor))
            self.setFocusPolicy(Qt.StrongFocus)
        else:
            self.unsetCursor()
            self.setFocusPolicy(Qt.NoFocus)
            self._set_hover(False)

    def mouseReleaseEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._clickable and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        if self._clickable:
            self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.hasFocus():
            self._set_hover(False)
        super().leaveEvent(event)

    def focusInEvent(self, event):
        if self._clickable:
            self._set_hover(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if not self.underMouse():
            self._set_hover(False)
        super().focusOutEvent(event)

    def _set_hover(self, on):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)


class DropOverlay(QWidget):
    """拖放遮罩：accent 虚线边框 + 居中提示，覆盖整个窗口。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("dropOverlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        label = QLabel("松开鼠标，收录文档")
        label.setObjectName("dropOverlayText")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self.hide()


class IngestWorker(QThread):
    WRITES_DATA = True  # 退出时必须等它结束（见 MainWindow._shutdown）
    progress = Signal(str)
    finished_all = Signal(list)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        # 文件收集（rglob）也在这里做，大目录/网络盘不卡 GUI；任何异常都要变成结果，
        # 否则 finished_all 不发，界面一直停在"正在收录"
        try:
            results = ingest.ingest_files(
                self.paths,
                progress_cb=lambda f, i, n: self.progress.emit(
                    f"正在收录 {os.path.basename(f)}（{i + 1}/{n}）..."
                ),
            )
        except Exception as e:
            results = [
                {"ok": False, "title": "收录过程", "error": f"{type(e).__name__}: {e}"}
            ]
        self.finished_all.emit(results)


class RemoveWorker(QThread):
    """后台删除文档（向量库删除在大库上要几十到几百毫秒，不放 GUI 线程）。"""

    WRITES_DATA = True
    done = Signal(bool, str)  # (成功, 失败原因)

    def __init__(self, doc_id, parent=None):
        super().__init__(parent)
        self.doc_id = doc_id

    def run(self):
        try:
            self.done.emit(ingest.remove_document(self.doc_id), "")
        except Exception as e:
            self.done.emit(False, f"{type(e).__name__}: {e}")


class AskWorker(QThread):
    done = Signal(dict)

    def __init__(self, question, parent=None):
        super().__init__(parent)
        self.question = question

    def run(self):
        try:
            result = qa.answer(self.question)
        except Exception as e:
            # 模型下载失败 / onnx / chroma 出错：给出可见答案并恢复输入框
            result = {"answer": f"检索失败：{type(e).__name__}: {e}", "sources": []}
        self.done.emit(result)


class WebWorker(QThread):
    """抓取网页正文并存为 markdown 笔记（网络请求在 worker 线程）。"""

    done = Signal(dict)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            title, markdown = web.fetch_webpage(self.url)
            path = web.save_webpage_note(title, markdown)
            self.done.emit({"ok": True, "title": title, "path": path})
        except web.FetchError as e:
            self.done.emit({"ok": False, "error": str(e)})
        except Exception as e:
            self.done.emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


class SelectionWorker(QThread):
    """读取资源管理器选中项：COM 跨进程调用放在工作线程，资源管理器卡住时不拖死主界面。"""

    done = Signal(object)  # list（路径）或 str（错误原因）

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        from ..explorer_selection import SelectionError, get_explorer_selected_paths

        try:
            self.done.emit(get_explorer_selected_paths(self.target))
        except SelectionError as e:
            self.done.emit(str(e))
        except Exception as e:
            self.done.emit(f"读取资源管理器选中项失败：{type(e).__name__}: {e}")


def _alive(obj) -> bool:
    """Qt 对象的 C++ 端是否还在（deleteLater 之后 Python 包装仍可能被引用）。"""
    try:
        import shiboken6

        return shiboken6.isValid(obj)
    except Exception:
        return True


# 不归 MainWindow._workers 管的线程：设置对话框里的查询/验证、退出时放弃等待的。
# 保持引用直到线程结束（否则 Python 包装被回收会销毁运行中的 QThread），_shutdown 也会扫描这里
_orphan_workers = []

# 退出时只读/联网任务（问答、抓网页、读选中项、验证 Key）最多再等这么久，之后直接放弃
_READONLY_GRACE_SEC = 1.5


def _track_side_worker(worker):
    if worker in _orphan_workers:
        return
    _orphan_workers.append(worker)
    worker.finished.connect(lambda: _orphan_workers.remove(worker) if worker in _orphan_workers else None)


def _detach_worker(worker):
    """断开结果回调并脱离父对象：父窗口/对话框析构时不连带销毁仍在运行的线程。"""
    if not (_alive(worker) and worker.isRunning()):
        return
    done = getattr(worker, "done", None)
    if done is not None:
        try:
            done.disconnect()
        except (RuntimeError, TypeError):
            pass
    worker.setParent(None)
    _track_side_worker(worker)


class MainWindow(QMainWindow):
    model_loading = Signal(bool)

    def __init__(self, splash=None):
        super().__init__()
        self._splash = splash  # 启动画面（可选）：构造期间分阶段更新状态文字
        self.setWindowTitle("个人助理知识库")
        from .icon import make_icon

        self.setWindowIcon(make_icon())
        self.setAcceptDrops(True)
        self.setStyleSheet(QSS)
        self._workers = []
        self._ingest_queue = []      # 待收录的路径批次（串行执行）
        self._ingest_running = False
        self._ingest_results = []    # 当前队列已完成批次的结果，队列清空时统一汇报
        self._ingest_toast = None
        self._web_toast = None       # 网页抓取单独一条 toast，不覆盖收录进度
        self._thinking_widget = None
        self._bubble_labels = []
        self._chat_rows = []         # 聊天区每一行的 (layout, widget)，超过上限时删最早的
        # 拖动窗口边缘时 resize 事件很密：停下 50ms 后再统一重排气泡宽度
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(50)
        self._refit_timer.timeout.connect(self._refit_bubbles)
        self._shutting_down = False  # 进入退出流程后拒绝新的收录请求
        self._shutdown_done = False
        self._selection_busy = False

        self._splash_status("正在初始化数据库…")
        db.init_db()
        embeddings.set_loading_callback(lambda dl: self.model_loading.emit(dl))
        self.model_loading.connect(self._on_model_loading)

        root = QWidget()
        root.setObjectName("centralRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        root_layout.addWidget(splitter)

        # ---------- 聊天区 ----------
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(8, 8, 8, 8)
        chat_layout.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.messages_widget = QWidget()
        self.messages_widget.setObjectName("messagesContainer")
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setContentsMargins(2, 2, 8, 2)
        self.messages_layout.setSpacing(9)
        self.messages_layout.addStretch(1)
        # 空状态欢迎面板：上下 stretch 居中；首条消息加入时移除
        self._welcome_panel = self._build_welcome_panel()
        self.messages_layout.addWidget(self._welcome_panel, 0, Qt.AlignHCenter)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages_widget)
        self.scroll.viewport().installEventFilter(self)
        chat_layout.addWidget(self.scroll, stretch=1)

        # 输入卡片
        self.input_card = QFrame()
        self.input_card.setObjectName("inputCard")
        self.input_card.setProperty("focused", False)
        card_layout = QHBoxLayout(self.input_card)
        card_layout.setContentsMargins(12, 6, 10, 6)
        card_layout.setSpacing(8)
        self.input = InputEdit()
        self.input.setObjectName("chatInput")
        self.input.setAccessibleName("提问输入框")
        # QPlainTextEdit 的 sizeHint 高约 6-7 行，不能交给布局决定高度；
        # 按文档行数动态定高：默认 1 行（卡片约 56px），随内容长高，上限 140px
        self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.input.submitted.connect(self._on_send)
        self.input.focus_changed.connect(self._on_input_focus)
        self.input.textChanged.connect(self._fit_input_height)
        self._fit_input_height()
        card_layout.addWidget(self.input, stretch=1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("sendButton")
        self.send_btn.setFixedHeight(32)
        self.send_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.send_btn.setToolTip("发送（回车）；Shift+回车换行")
        self.send_btn.setAccessibleName("发送问题")
        self.send_btn.clicked.connect(self._on_send)
        card_layout.addWidget(self.send_btn, 0, Qt.AlignBottom)
        chat_layout.addWidget(self.input_card)
        splitter.addWidget(chat_widget)

        # ---------- 侧栏 ----------
        side_widget = QWidget()
        side_widget.setObjectName("sidebar")
        side_layout = QVBoxLayout(side_widget)
        side_layout.setContentsMargins(8, 12, 8, 8)
        side_layout.setSpacing(6)
        title = QLabel("最近收录")
        title.setObjectName("sidebarTitle")
        side_layout.addWidget(title)
        self.doc_list = QListWidget()
        self.doc_list.setObjectName("docList")
        self.doc_list.setSpacing(4)
        self.doc_list.setFrameShape(QFrame.NoFrame)
        self.doc_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.doc_list.setAccessibleName("最近收录的文档")
        # itemActivated：双击和键盘 Enter 都会触发
        self.doc_list.itemActivated.connect(self._on_doc_double_clicked)
        self.doc_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._on_doc_context_menu)
        del_sc = QShortcut(QKeySequence.Delete, self.doc_list)
        del_sc.setContext(Qt.WidgetShortcut)
        del_sc.activated.connect(lambda: self._confirm_remove(self.doc_list.currentItem()))
        side_layout.addWidget(self.doc_list, stretch=1)
        self.empty_hint = QLabel("还没有收录文档\n拖文件进来试试")
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(self.empty_hint, stretch=1)
        hint = QLabel("双击或回车打开 · 右键 / Delete 移除")
        hint.setStyleSheet(f"color: {SUBTLE}; font-size: 12px; background: transparent;")
        hint.setAlignment(Qt.AlignCenter)
        side_layout.addWidget(hint)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("settingsButton")
        self.settings_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.settings_btn.setToolTip("设置（Ctrl+,）：监控文件夹、开机自启、AI 问答、快捷键")
        self.settings_btn.setAccessibleName("打开设置")
        self.settings_btn.clicked.connect(lambda: self._open_watch_settings())
        self.hotkeys_btn = QPushButton("快捷键")
        self.hotkeys_btn.setObjectName("settingsButton")
        self.hotkeys_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.hotkeys_btn.setToolTip("修改全局快捷键")
        self.hotkeys_btn.setAccessibleName("修改全局快捷键")
        self.hotkeys_btn.clicked.connect(self._open_hotkey_settings)
        footer = QHBoxLayout()
        footer.setSpacing(4)
        footer.addStretch(1)
        footer.addWidget(self.settings_btn)
        footer.addWidget(self.hotkeys_btn)
        footer.addStretch(1)
        side_layout.addLayout(footer)
        splitter.addWidget(side_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([860, 240])
        splitter.setCollapsible(1, False)

        # 拖放遮罩（顶层子控件，随窗口 resize）
        self.overlay = DropOverlay(self)

        # 输入框有焦点时 Ctrl+V 由输入框自己处理（文字进输入框，文件/截图收录）；
        # 焦点在窗口其他地方（聊天区、侧栏）时，剪贴板里的任何内容都收录
        paste_sc = QShortcut(QKeySequence.Paste, self)
        paste_sc.setContext(Qt.WindowShortcut)
        paste_sc.activated.connect(lambda: self._ingest_clipboard(QApplication.clipboard().mimeData()))
        self.input.ingest_requested.connect(self._ingest_clipboard)
        settings_sc = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_sc.setContext(Qt.WindowShortcut)
        settings_sc.activated.connect(lambda: self._open_watch_settings())
        focus_sc = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_sc.setContext(Qt.WindowShortcut)
        focus_sc.activated.connect(lambda: self.input.setFocus(Qt.ShortcutFocusReason))
        self._refresh_doc_list()

        # V1.1：监控文件夹 + 全局热键
        self._splash_status("正在启动后台服务…")
        self.watcher = FolderWatcher(self)
        self.watcher.files_ready.connect(self._start_ingest)
        self.watcher.start(config.get_watch_folders())

        self.hotkey = None
        self._start_hotkeys()

        # 单实例 IPC：右键菜单 --add 转发收录 / 无参二次启动激活窗口
        # quit：卸载程序请求正常退出（先等收录写完，避免强杀损坏数据）
        self.ipc_server = ipc.IpcServer(self, accept=("paths", "activate", "quit"))
        self.ipc_server.message_received.connect(self._on_ipc_message)
        self.ipc_server.failed.connect(self._on_ipc_failed)
        self.ipc_server.start()

        # 系统托盘：关闭窗口最小化到托盘，菜单"退出"才真正退出
        self._quitting = False
        self._tray_notified = False
        self.tray = None
        app = QApplication.instance()
        if app is not None:
            # 任何退出路径（托盘退出、注销/关机、app.quit）都走统一清理
            app.aboutToQuit.connect(self._shutdown)
            # 注销/关机：Windows 发 WM_QUERYENDSESSION，Qt 会逐个关窗口；closeEvent 里有托盘时
            # 会 ignore，可能被当成"阻止关机"。收到会话结束请求就按真正退出处理
            app.commitDataRequest.connect(self._on_session_end)
        if QSystemTrayIcon.isSystemTrayAvailable():
            if app is not None:
                app.setQuitOnLastWindowClosed(False)
            from .icon import make_icon

            self.tray = QSystemTrayIcon(make_icon(), self)  # 眼睛 logo
            self.tray.setToolTip("个人助理知识库")
            tray_menu = QMenu()
            act_show = tray_menu.addAction("显示主窗口")
            act_show.triggered.connect(self._restore_from_tray)
            act_quit = tray_menu.addAction("退出")
            act_quit.triggered.connect(self._quit_app)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()

    # ---------- 布局辅助 ----------

    def _splash_status(self, text: str):
        """构造期间向 splash 报告阶段状态（无 splash 时静默跳过）。"""
        if self._splash is not None:
            self._splash.show_status(text)

    def eventFilter(self, obj, event):
        # 聊天视口尺寸变化时，气泡宽度按视口 82% 上限重新适配（只有宽度变化才需要）
        if obj is self.scroll.viewport() and event.type() == event.Type.Resize:
            if event.oldSize().width() != event.size().width():
                self._refit_timer.start()
        return super().eventFilter(obj, event)

    def _refit_bubbles(self):
        self._bubble_labels = [
            (label, est) for label, est in self._bubble_labels if _alive(label)
        ]
        for label, est in self._bubble_labels:
            self._fit_bubble_label(label, est)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(0, 0, self.width(), self.height())

    def _scroll_to_bottom(self):
        # 很高的 markdown 气泡要等布局完成，滚动条 maximum 才更新：等下一次 rangeChanged
        # 再滚一次（一次性连接）；内容没撑出新高度时 rangeChanged 不来，singleShot(0) 兜底
        bar = self.scroll.verticalScrollBar()

        def _to_end(*_):
            try:
                bar.rangeChanged.disconnect(_to_end)
            except (RuntimeError, TypeError):
                pass
            bar.setValue(bar.maximum())

        bar.rangeChanged.connect(_to_end)
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _add_row(self, widget, align):
        self._dismiss_welcome_panel()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if align == "right":
            row.addStretch(1)
            row.addWidget(widget)
        elif align == "left":
            row.addWidget(widget)
            row.addStretch(1)
        else:  # center
            row.addStretch(1)
            row.addWidget(widget)
            row.addStretch(1)
        self.messages_layout.insertLayout(self.messages_layout.count() - 1, row)
        self._chat_rows.append((row, widget))
        self._trim_chat_history()
        self._scroll_to_bottom()

    _MAX_CHAT_ROWS = 200

    def _trim_chat_history(self):
        """聊天记录只保留最近 _MAX_CHAT_ROWS 行：托盘常驻时气泡无限增长会拖慢 resize 重排。"""
        while len(self._chat_rows) > self._MAX_CHAT_ROWS:
            row, widget = self._chat_rows.pop(0)
            if widget is self._thinking_widget:
                self._thinking_widget = None
            self.messages_layout.removeItem(row)
            if _alive(widget):
                # 气泡内的 label 也要移出重排列表（deleteLater 之前它们仍然 _alive）
                self._bubble_labels = [
                    (label, est) for label, est in self._bubble_labels
                    if _alive(label) and not widget.isAncestorOf(label)
                ]
                widget.hide()
                widget.deleteLater()
            row.deleteLater()

    def _dismiss_welcome_panel(self):
        """首条消息加入时移除欢迎面板（单向隐藏，本次运行内不再出现）。"""
        if self._welcome_panel is not None:
            self.messages_layout.removeWidget(self._welcome_panel)
            self._welcome_panel.hide()
            self._welcome_panel.deleteLater()
            self._welcome_panel = None
            self._llm_card = None
            self._hotkey_strip_label = None

    def _build_welcome_panel(self):
        panel = QWidget()
        panel.setObjectName("welcomePanel")
        panel.setMaximumWidth(700)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("你好，我是你的知识库助理")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("拖入或 Ctrl+V 粘贴文件即可收录，然后直接用自然语言提问")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        cards_row.addStretch(1)
        add_card = HintCard("添加文件", "点击选择文件，或把文件/整个文件夹直接拖进窗口")
        add_card.clicked.connect(self._pick_files_to_ingest)
        ask_card = HintCard("直接提问", "点击填入示例：" + _EXAMPLE_QUESTION)
        ask_card.clicked.connect(self._fill_example_question)
        self._llm_card = HintCard("", "")
        self._llm_card.clicked.connect(lambda: self._open_watch_settings(focus_api_key=True))
        self._refresh_llm_card()
        for card in (add_card, ask_card, self._llm_card):
            cards_row.addWidget(card)
        cards_row.addStretch(1)
        layout.addLayout(cards_row)
        layout.addSpacing(6)
        layout.addWidget(self._build_hotkey_strip(), 0, Qt.AlignHCenter)
        return panel

    def _build_hotkey_strip(self):
        """欢迎面板底部：当前全局快捷键一览 + "修改"入口（启动即可见、可配置）。"""
        strip = QFrame()
        strip.setObjectName("hotkeyStrip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(12)
        title = QLabel("全局快捷键")
        title.setObjectName("hotkeyStripTitle")
        row.addWidget(title)
        self._hotkey_strip_label = QLabel()
        self._hotkey_strip_label.setObjectName("hotkeyStripText")
        self._hotkey_strip_label.setTextFormat(Qt.RichText)
        row.addWidget(self._hotkey_strip_label)
        edit_btn = QPushButton("修改")
        edit_btn.setObjectName("linkButton")
        edit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        edit_btn.setAccessibleName("修改全局快捷键")
        edit_btn.clicked.connect(self._open_hotkey_settings)
        row.addWidget(edit_btn)
        return strip

    def _hotkey_summary_html(self) -> str:
        parts = []
        for action, (_, desc) in config.HOTKEY_ACTIONS.items():
            key = hotkey.display(self._hotkeys.get(action, ""))
            key_html = (f"<span style='color:{TEXT}; font-weight:600;'>{escape(key)}</span>"
                        if key else f"<span style='color:{SUBTLE};'>未设置</span>")
            parts.append(f"{escape(desc)} {key_html}")
        return "<span style='color:%s;'>%s</span>" % (SUBTLE, " &nbsp;·&nbsp; ".join(parts))

    def _refresh_hotkey_texts(self):
        """快捷键变化后刷新所有提到它的文字（输入框提示、状态栏、欢迎面板）。"""
        clip = hotkey.display(self._hotkeys.get("clipboard", ""))
        sel = hotkey.display(self._hotkeys.get("selection", ""))
        tips = ["输入问题，回车发送", "拖入或粘贴文件收录"]
        show = hotkey.display(self._hotkeys.get("show", ""))
        if show:
            tips.append(f"{show} 随时呼出")
        if sel:
            tips.append(f"{sel} 收录选中项")
        if clip:
            tips.append(f"{clip} 存剪贴板")
        self.input.setPlaceholderText("；".join(tips))
        self.statusBar().showMessage(
            "就绪（拖入或 Ctrl+V 粘贴文件即可收录"
            + (f"；{clip} 保存剪贴板" if clip else "") + "）"
        )
        label = getattr(self, "_hotkey_strip_label", None)
        if label is not None:
            label.setText(self._hotkey_summary_html())

    def _refresh_llm_card(self):
        """第三张卡随 API Key 配置状态变化（设置保存后立即刷新，不必重启）。"""
        card = getattr(self, "_llm_card", None)
        if card is None:
            return
        if llm.has_llm():
            card.set_content("✓ 智能回答已就绪", "回答将附带来源引用", clickable=False)
        else:
            card.set_content("配置 API Key", "点击填写，解锁智能回答（当前仅返回原文片段）")

    def _pick_files_to_ingest(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要收录的文件")
        if paths:
            self._start_ingest(paths)

    def _fill_example_question(self):
        """只填入不发送：知识库还空时自动发送只会得到"未找到"。"""
        self.input.setPlainText(_EXAMPLE_QUESTION)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)

    # ---------- 消息渲染 ----------

    def _make_bubble_label(self, text, is_markdown=False):
        if is_markdown:
            # LLM 回答：Qt 自带解析器渲染 Markdown（原始 HTML/图片/非 http 链接已在 render 里屏蔽）
            html, natural_w = markdown.render(text, font_px=theme.BODY_PX, link_color=ACCENT)
            label = QLabel(html)
            label.linkActivated.connect(self._open_link)
            est = int(natural_w) + 10
        else:
            label = QLabel(escape(text).replace("\n", "<br>"))
        # 已转义为 HTML：必须显式 RichText，否则单行文本被自动判为纯文本，"&" 会显示成 "&amp;"
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        if not is_markdown:
            # wordwrap QLabel 的 sizeHint 宽度不可靠（短文本会挤成窄气泡），
            # 按最长行估算内容宽度，钳制到视口 82%；label 此时未挂入窗口树，
            # QSS 字号未生效，需显式用正文字号度量
            font = label.font()
            font.setPixelSize(theme.BODY_PX)
            fm = QFontMetrics(font)
            est = max((fm.horizontalAdvance(line) for line in text.split("\n")), default=0) + 10
        self._bubble_labels.append((label, est))
        self._fit_bubble_label(label, est)
        return label

    def _fit_bubble_label(self, label, est):
        max_w = int(self.scroll.viewport().width() * 0.82)
        if max_w <= 0:
            return
        label.setFixedWidth(min(est, max_w))

    def _append_user(self, text):
        bubble = QFrame()
        bubble.setObjectName("userBubble")
        bubble.setAccessibleName("你：" + text[:60])
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self._make_bubble_label(text))
        self._add_row(bubble, "right")

    def _add_system_notice(self, text):
        text = text.replace("\n", "　")
        label = QLabel(escape(text))
        label.setTextFormat(Qt.RichText)
        label.setObjectName("systemNotice")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        # 同气泡：wordwrap QLabel 的 sizeHint 会把长消息挤成小方块，按文字宽度估算，
        # 钳制到视口 82%（随窗口缩放重排）；12px 字号 + 左右 padding 12*2
        font = label.font()
        font.setPixelSize(12)
        est = QFontMetrics(font).horizontalAdvance(text) + 24 + 6
        self._bubble_labels.append((label, est))
        self._fit_bubble_label(label, est)
        self._add_row(label, "center")
        return label

    def _add_assistant(self, text, sources, is_markdown=False):
        bubble = QFrame()
        bubble.setObjectName("assistantBubble")
        bubble.setAccessibleName("助理：" + text[:60])
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addWidget(self._make_bubble_label(text, is_markdown))
        if sources:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"color: {BORDER}; background: {BORDER}; border: none;")
            sep.setFixedHeight(1)
            layout.addWidget(sep)
            header = QLabel("出处")
            header.setStyleSheet(f"color: {SUBTLE}; font-size: 12px; background: transparent;")
            layout.addWidget(header)
            for s in sources:
                # QUrl.fromLocalFile 会正确编码 # % 空格等；再 escape 防止 ' " 截断属性
                url = escape(QUrl.fromLocalFile(s["file_path"]).toString(QUrl.FullyEncoded)).replace('"', "&quot;")
                link = QLabel(
                    f"<a href=\"{url}\" style='color: {ACCENT}; text-decoration: none;'>"
                    f"［{s['index']}］{escape(s['title'])}</a>"
                    f"<span style='color: {SUBTLE};'> — {escape(s['snippet'])}…</span>"
                )
                link.setTextFormat(Qt.RichText)
                link.setObjectName("sourceLink")
                link.setWordWrap(True)
                link.setTextInteractionFlags(Qt.TextBrowserInteraction)
                link.setCursor(QCursor(Qt.PointingHandCursor))
                link.linkActivated.connect(self._open_link)
                layout.addWidget(link)
        self._add_row(bubble, "left")
        return bubble

    # ---------- 拖放入库 ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.overlay.setGeometry(0, 0, self.width(), self.height())
            self.overlay.show()
            self.overlay.raise_()

    def dragLeaveEvent(self, event):
        self.overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.overlay.hide()
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self._start_ingest(paths)

    @staticmethod
    def _progress_toast(toast, text, **kw):
        """toast 还在就就地更新，已关闭（兜底超时/淡出）则新建；返回当前 toast。

        kw 为空：进度态；带 success=：结果态。
        """
        pending = not kw
        if toast is not None and toast.is_alive():
            toast.update(text, pending=pending, **kw)
            return toast
        return show_progress(text) if pending else show_toast(text, **kw)

    def _start_ingest(self, paths: list):
        """统一的入库入口：拖放 / 监控文件夹 / IPC 转发 / 剪贴板热键共用。

        所有请求排队、同一时间只跑一个 IngestWorker（ingest 内部也有锁兜底），
        进度 toast 只在整条队列跑完时才变成结果。
        """
        if self._shutting_down:
            return  # 正在退出：不再启动新的 worker（IPC 已停，转发方会收到 busy 自行处理）
        paths = [str(p) for p in paths if p]
        if not paths:
            return
        self._ingest_queue.append(paths)
        if len(paths) == 1 and not os.path.isdir(paths[0]):
            progress_text = f"正在收录《{os.path.basename(paths[0])}》…"
        else:
            progress_text = "正在收录…"
        self._ingest_toast = self._progress_toast(self._ingest_toast, progress_text)
        self.statusBar().showMessage(progress_text)
        self._run_next_ingest()

    def _run_next_ingest(self):
        if self._shutting_down or self._ingest_running or not self._ingest_queue:
            return
        batch = []
        while self._ingest_queue:  # 排队中的请求合并成一批
            batch.extend(self._ingest_queue.pop(0))
        self._ingest_running = True
        worker = IngestWorker(batch, parent=self)
        worker.progress.connect(self.statusBar().showMessage)
        worker.finished_all.connect(self._on_ingest_finished)
        self._keep_worker(worker)
        worker.start()

    def _on_ingest_finished(self, results):
        self._ingest_running = False
        if self._shutting_down:
            return
        self._ingest_results.extend(results)
        if self._ingest_queue:
            self._run_next_ingest()
            return
        results, self._ingest_results = self._ingest_results, []
        self._report_ingest(results)

    _NOTICE_MAX_ITEMS = 10

    def _report_ingest(self, results):
        ok = [r for r in results if r["ok"] and not r.get("skipped")]
        kept_old = [r for r in results if r.get("skipped") and r.get("kept_old")]
        skipped = [r for r in results if r.get("skipped") and not r.get("kept_old")]
        failed = [r for r in results if not r["ok"]]
        parts = []
        for r in ok:
            mark = "（已更新）" if r.get("replaced") else ""
            if r.get("archived_only"):
                parts.append(f"{r['title']}{mark}：已归档（未提取内容，按文件名检索）")
            else:
                parts.append(f"{r['title']}{mark}：{r['chunk_count']} 个片段")
            if r.get("warning"):
                parts[-1] += f"（{r['warning']}）"
        for r in kept_old:
            parts.append(f"{r['title']}：{r.get('warning') or '新版本未能提取文本，已保留旧版本'}")
        for r in failed:
            parts.append(f"{r['title']} 收录失败：{r['error']}")
        if parts:
            # 拖入大文件夹时可能上千条：消息里只列前几条，完整清单放 tooltip
            if len(parts) > self._NOTICE_MAX_ITEMS:
                summary = (f"收录完成（成功 {len(ok)}，保留旧版本 {len(kept_old)}，"
                           f"失败 {len(failed)}，未变化 {len(skipped)}）· ")
                shown = "；".join(parts[:self._NOTICE_MAX_ITEMS])
                notice = self._add_system_notice(
                    f"{summary}{shown}；…另有 {len(parts) - self._NOTICE_MAX_ITEMS} 项（悬停查看全部）"
                )
                notice.setToolTip("\n".join(parts[:500])
                                  + (f"\n…共 {len(parts)} 项" if len(parts) > 500 else ""))
            else:
                self._add_system_notice("收录完成 · " + "；".join(parts))
        elif not skipped:
            self._add_system_notice("没有收录任何文件")
        if parts:
            self.statusBar().showMessage(
                f"收录完成：成功 {len(ok)} 个，跳过 {len(skipped)} 个，"
                f"保留旧版本 {len(kept_old)} 个，失败 {len(failed)} 个"
            )
        elif skipped:
            self.statusBar().showMessage(f"文件未变化，跳过收录（{len(skipped)} 个）")
        else:
            self.statusBar().showMessage("没有收录任何文件")
        self._refresh_doc_list()
        # 进度 toast 就地更新为结果；已被关闭（兜底超时等）则新建
        if failed:
            text, success = (f"{len(failed)} 个文件收录失败：{failed[0]['error']}", False)
        elif kept_old and not ok:
            r = kept_old[0]
            text = f"《{r['title']}》：{r.get('warning') or '新版本未能提取文本，已保留旧版本'}"
            success = False
        elif ok:
            if len(ok) == 1:
                if ok[0].get("archived_only"):
                    text = f"已归档《{ok[0]['title']}》（未提取内容，可按文件名检索）"
                else:
                    text = f"已收录《{ok[0]['title']}》（共 {ok[0]['chunk_count']} 个片段）"
            else:
                text = f"已收录 {len(ok)} 个文档"
            success = True
        elif skipped:
            text, success = ("文件未变化，跳过收录", True)
        else:
            text, success = ("没有收录任何文件", False)
        self._ingest_toast = self._progress_toast(self._ingest_toast, text, success=success)

    # ---------- 监控文件夹 / 剪贴板热键 ----------

    def _open_watch_settings(self, focus_api_key: bool = False):
        dialog = WatchFoldersDialog(self._hotkeys, self)
        try:
            if focus_api_key:
                dialog.focus_api_key()
            if dialog.exec() != QDialog.Accepted:
                return
            self._save_settings(dialog)
        finally:
            # 托盘常驻：每次打开都新建对话框，不释放会一直挂在主窗口下累积
            dialog.deleteLater()

    def _save_settings(self, dialog):
        # 两项设置互相独立：一项失败不影响另一项，失败原因都要让用户看到
        errors = []
        folders = dialog.folders()
        folders_saved = False
        if folders != config.get_watch_folders():
            try:
                config.save_watch_folders(folders)
                folders_saved = True
            except (config.ConfigError, OSError) as e:
                # 配置损坏时拒绝覆盖（否则 api_key 等会被清掉）；只读目录等写入失败也要让用户看到
                errors.append(f"监控文件夹未保存：{e}")
        # 开机自启开关保存即生效
        # 勾选时总是重写（幂等），顺便把旧版不带 --minimized 的命令升级
        if dialog.autostart_checked() or config.autostart_enabled():
            try:
                config.set_autostart(dialog.autostart_checked())
            except OSError as e:
                errors.append(f"开机自动启动设置失败：{e}")
        llm_values = dialog.llm_values()
        old_llm = config.get_llm_file_config()
        if llm_values["api_key"] is None:  # AI 设置没改：沿用已保存的值，只可能改了自动标签开关
            llm_values.update(old_llm)
        llm_changed = (
            any(llm_values[k] != old_llm[k] for k in ("api_key", "base_url", "model", "model_auto"))
            or llm_values["auto_tags"] != config.auto_tags_enabled()
        )
        if llm_changed:
            try:
                config.save_llm_config(**llm_values)
            except (config.ConfigError, OSError) as e:
                errors.append(f"AI 问答设置未保存：{e}")
            self._refresh_llm_card()
        if folders_saved:
            self.watcher.restart()
        err = self._apply_hotkeys(dialog.hotkey_values())
        if err:
            errors.append(err)
        if errors:
            QMessageBox.warning(self, "保存失败", "\n".join(errors))
            self.statusBar().showMessage(errors[0])
        elif folders:
            self.statusBar().showMessage(
                f"设置已保存（监控 {len(folders)} 个文件夹，{self.watcher.backend_name()}）"
            )
        else:
            self.statusBar().showMessage("设置已保存（未监控任何文件夹）")

    def _apply_hotkeys(self, new: dict):
        """保存并重新注册快捷键（设置对话框与快捷键对话框共用）。

        返回 None 表示成功或没变化，否则返回错误文字。
        """
        if new == self._hotkeys:
            return None
        try:
            config.save_hotkeys(new)
        except (config.ConfigError, OSError) as e:
            return f"快捷键未保存：{e}"
        self._start_hotkeys()
        return None

    def _start_hotkeys(self):
        """按配置注册全局快捷键；改键后再次调用即替换（旧线程先注销再起新线程）。"""
        if self.hotkey is not None:
            old = self.hotkey
            old.stop()
            if not old.isRunning():
                old.deleteLater()  # 已退出的旧线程释放；没停下来的已由 stop() 脱离父对象托管
        self._hotkeys = config.get_hotkeys()
        self.hotkey = hotkey.HotkeyThread(self._hotkeys, self)
        self.hotkey.triggered.connect(self._on_hotkey)
        self.hotkey.selection_ingest_requested.connect(self._on_selection_ingest)
        self.hotkey.show_requested.connect(self._toggle_from_hotkey)
        # 注册失败要让用户看得到（状态栏消息很快被覆盖，开机自启时更看不到）
        self.hotkey.failed.connect(self._on_hotkey_failed)
        self.hotkey.start()
        self._refresh_hotkey_texts()

    def _open_hotkey_settings(self):
        dialog = HotkeyDialog(self._hotkeys, self)
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            new = dialog.values()
        finally:
            dialog.deleteLater()
        if new == self._hotkeys:
            return
        err = self._apply_hotkeys(new)
        if err:
            QMessageBox.warning(self, "保存失败", err)
            return
        show_toast("快捷键已更新")

    def _on_hotkey_failed(self, msg: str):
        self.statusBar().showMessage(msg)
        self._add_system_notice(msg)

    def _on_hotkey(self):
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text().strip()
        if not text:
            self.statusBar().showMessage("剪贴板没有文本")
            return
        path = ingest.save_clipboard_note(text)
        self.statusBar().showMessage("已保存剪贴板笔记，正在收录...")
        self._start_ingest([path])

    def _on_selection_ingest(self):
        """全局快捷键（默认 Ctrl+Shift+A）：收录资源管理器（或桌面）当前选中的文件/文件夹。"""
        from ..explorer_selection import foreground_target

        # 前台窗口必须在热键触发的当下取；COM 读取选中项放到工作线程
        target = foreground_target()
        if target is None:
            show_toast("请先在资源管理器中选中要收录的文件", success=False)
            self.statusBar().showMessage("前台不是资源管理器或桌面")
            return
        if self._selection_busy:
            return  # 上一次还在读取（资源管理器卡住时）：忽略连按
        self._selection_busy = True
        worker = SelectionWorker(target, parent=self)
        worker.done.connect(self._on_selection_read)
        self._keep_worker(worker)
        worker.start()

    def _on_selection_read(self, result):
        self._selection_busy = False
        if isinstance(result, str):
            show_toast(result, success=False)
            self.statusBar().showMessage(result)
            return
        if not result:
            show_toast("请先在资源管理器中选中要收录的文件", success=False)
            self.statusBar().showMessage("资源管理器中没有选中项")
            return
        self._start_ingest(result)

    # ---------- 问答 ----------

    def _on_send(self):
        question = self.input.toPlainText().strip()
        if not question:
            return
        self.input.clear()
        # 整句是一个裸 URL：抓取网页正文收录，而不是提问
        if web.is_bare_url(question):
            self._append_user(question)
            self._start_web_fetch(question)
            return
        self._append_user(question)
        self._thinking_widget = self._add_system_notice("思考中...")
        self.input.setEnabled(False)
        self.send_btn.setEnabled(False)

        worker = AskWorker(question, parent=self)
        worker.done.connect(self._on_answer)
        self._keep_worker(worker)
        worker.start()

    def _start_web_fetch(self, url: str):
        # 独立 toast：抓取失败时不会把正在进行的收录进度改成失败态
        self._web_toast = self._progress_toast(self._web_toast, "正在抓取网页…")
        self.statusBar().showMessage(f"正在抓取 {url} ...")
        worker = WebWorker(url, parent=self)
        worker.done.connect(self._on_web_fetched)
        self._keep_worker(worker)
        worker.start()

    def _on_web_fetched(self, result: dict):
        if not result["ok"]:
            self._web_toast = self._progress_toast(
                self._web_toast, f"网页抓取失败：{result['error']}", success=False
            )
            self.statusBar().showMessage(f"网页抓取失败：{result['error']}")
            return
        if self._web_toast is not None and self._web_toast.is_alive():
            self._web_toast.close()  # 接下来由收录进度 toast 接手
        self._web_toast = None
        self._add_system_notice(f"已抓取网页《{result['title']}》，正在收录")
        self._start_ingest([result["path"]])

    def _on_answer(self, result):
        self._remove_thinking()
        self._add_assistant(result["answer"], result.get("sources", []),
                            result.get("markdown", False))
        self.input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.input.setFocus()
        self.statusBar().showMessage("就绪")

    def _remove_thinking(self):
        if self._thinking_widget is not None:
            self._thinking_widget.setParent(None)
            self._thinking_widget.deleteLater()
            self._thinking_widget = None

    # ---------- 交互 ----------

    def _open_link(self, url):
        qurl = QUrl(url)
        if qurl.scheme() in ("http", "https"):
            # 回答里的网页链接（markdown.render 只保留 http/https）交给默认浏览器
            QDesktopServices.openUrl(qurl)
            return
        path = qurl.toLocalFile()
        if path and os.path.isfile(path):
            os.startfile(path)

    def _fit_input_height(self):
        """按输入内容行数调整输入框高度：1 行 44px 起步，每行约 lineSpacing，上限 140px。"""
        line_h = self.input.fontMetrics().lineSpacing()
        lines = max(1, self.input.document().lineCount())
        h = max(44, min(140, lines * line_h + 14))
        self.input.setFixedHeight(h)

    def _on_input_focus(self, focused):
        self.input_card.setProperty("focused", focused)
        self.input_card.style().unpolish(self.input_card)
        self.input_card.style().polish(self.input_card)

    def _ingest_clipboard(self, mime):
        """Ctrl+V 收录：文件 → 收录文件；文字 → 网址抓网页 / 其余存为笔记；截图 → 存 PNG 走 OCR。

        顺序是文件 > 文字 > 图片：从 Word/Excel 复制时剪贴板同时带文字和一张渲染图，
        应该收录文字；只有截图这种纯图片才当图片收录。
        """
        if mime is None:
            return
        files = clipboard_files(mime)
        if files:
            self._start_ingest(files)
            return
        text = mime.text().strip() if mime.hasText() else ""
        if text:
            if web.is_bare_url(text):
                self._append_user(text)
                self._start_web_fetch(text)
                return
            path = ingest.save_clipboard_note(text)
            self._add_system_notice(f"已粘贴文字（{len(text)} 字），正在收录")
            self._start_ingest([path])
            return
        if mime.hasImage():
            image = mime.imageData()
            if image is not None and not image.isNull():
                path = ingest.new_note_path("截图", ".png")
                if image.save(path, "PNG"):
                    self._add_system_notice("已粘贴图片，正在识别文字并收录")
                    self._start_ingest([path])
                    return
        self.statusBar().showMessage("剪贴板里没有可收录的内容")

    def _on_doc_context_menu(self, pos):
        item = self.doc_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        open_act = menu.addAction("打开原文件")
        remove_act = menu.addAction("从知识库移除")
        chosen = menu.exec(self.doc_list.viewport().mapToGlobal(pos))
        menu.deleteLater()  # 每次右键都新建，用完释放
        if chosen is open_act:
            self._on_doc_double_clicked(item)
        elif chosen is remove_act:
            self._confirm_remove(item)

    def _confirm_remove(self, item):
        if item is None or self._shutting_down:
            return
        doc_id = item.data(Qt.UserRole + 1)
        title = item.data(Qt.UserRole + 2) or ""
        if doc_id is None:
            return
        ret = QMessageBox.question(
            self, "从知识库移除",
            f"确定移除《{title}》吗？\n\n将删除它在知识库中的索引和归档副本，之后提问不会再用到它。"
            "\n你电脑上的原文件不受影响。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.statusBar().showMessage(f"正在移除《{title}》…")
        worker = RemoveWorker(doc_id, parent=self)
        worker.done.connect(lambda ok, err, t=title: self._on_removed(t, ok, err))
        self._keep_worker(worker)
        worker.start()

    def _on_removed(self, title, ok, err):
        if self._shutting_down:
            return
        self._refresh_doc_list()
        if ok:
            self._add_system_notice(f"已从知识库移除《{title}》")
            self.statusBar().showMessage(f"已移除《{title}》")
        elif err:
            show_toast(f"移除失败：{err}", success=False)
            self.statusBar().showMessage(f"移除《{title}》失败：{err}")
        else:
            self.statusBar().showMessage(f"《{title}》已不在知识库中")

    def _on_doc_double_clicked(self, item):
        path = item.data(Qt.UserRole)
        if path and os.path.isfile(path):
            os.startfile(path)

    def _on_model_loading(self, downloading: bool):
        if downloading:
            self.statusBar().showMessage("首次使用正在下载模型（约 180MB），请稍候...")
        else:
            self.statusBar().showMessage("正在加载语义模型...")

    def _refresh_doc_list(self):
        self.doc_list.clear()
        docs = db.list_recent_documents(20)
        self.empty_hint.setVisible(not docs)
        self.doc_list.setVisible(bool(docs))
        for doc in docs:
            t = time.strftime("%m-%d %H:%M", time.localtime(doc["created_at"]))
            meta = f"{t} · {doc['chunk_count']} 个片段"
            tags = [x for x in (doc.get("tags") or "").split(",") if x.strip()]
            if tags:
                meta += "  " + " ".join(f"#{x.strip()}" for x in tags[:3])
            item = QListWidgetItem()
            # 内容由 setItemWidget 显示，item 本身无文字：给读屏软件一份
            item.setData(Qt.AccessibleTextRole, f"{doc['title']}，{meta}")
            item.setData(Qt.UserRole, doc["file_path"])
            item.setData(Qt.UserRole + 1, doc["id"])
            item.setData(Qt.UserRole + 2, doc["title"])
            item.setToolTip(doc["file_path"])
            card = DocCard(doc["title"], meta)
            item.setSizeHint(card.sizeHint())
            self.doc_list.addItem(item)
            self.doc_list.setItemWidget(item, card)

    def _keep_worker(self, worker):
        self._workers.append(worker)

        def _done():
            if worker in self._workers:
                self._workers.remove(worker)
            worker.deleteLater()  # 以 self 为父对象，不释放会随运行时间累积

        worker.finished.connect(_done)

    def _on_ipc_message(self, msg: dict):
        """IPC 消息：activate 激活窗口；quit 正常退出；paths 转发收录（结果走系统通知 + 状态栏）。"""
        try:
            if msg.get("action") == "quit":
                # 卸载程序请求退出：走与托盘"退出"相同的完整清理（等收录写完）
                QTimer.singleShot(0, self._quit_app)
                return
            if msg.get("action") == "activate":
                self._restore_from_tray()
                self.statusBar().showMessage("知识库助理已在运行")
                return
            paths = msg.get("paths")
            if isinstance(paths, list) and paths:
                # 不打扰用户：不弹主窗口，后台收录，结果由屏幕中央 toast 反馈
                self.statusBar().showMessage(
                    f"收到右键菜单添加请求（{len(paths)} 项），正在收录..."
                )
                self._start_ingest([str(p) for p in paths])
        except Exception as e:
            self.statusBar().showMessage(f"IPC 消息处理失败：{e}")

    def _on_ipc_failed(self, msg: str):
        self.statusBar().showMessage(msg)
        self._add_system_notice(msg)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self):
        # 每次进入主界面都最大化（托盘双击/菜单、二次启动激活、全局快捷键共用此入口）
        self.showMaximized()
        self.raise_()
        self.activateWindow()

    def _toggle_from_hotkey(self):
        """全局快捷键（默认 Ctrl+Alt+Space）：呼出主界面并聚焦输入框；已在前台时再按一次隐藏。"""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            if self.tray is not None:
                self.hide()
            else:
                self.showMinimized()
            return
        self._restore_from_tray()
        # 收到 WM_HOTKEY 的进程有权抢前台；Qt 的 activateWindow 有时只闪任务栏，再直接调一次 Win32
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        except Exception:
            pass
        if self.input.isEnabled():
            self.input.setFocus(Qt.ShortcutFocusReason)
            self.input.selectAll()  # 上次没发出去的问题保留，直接打字即覆盖

    def start_in_tray(self):
        """开机自启（--minimized）：不显示主窗口，只留托盘图标。"""
        self._tray_notified = True  # 不弹"已最小化到托盘"
        self.hide()
        self.statusBar().showMessage("已在后台运行")

    def _quit_app(self):
        if self._quitting:
            return  # 等待后台任务期间再次点"退出"：忽略，避免 closeEvent 嵌套
        self._quitting = True
        self.close()
        # 有托盘时设了 setQuitOnLastWindowClosed(False)，光 close() 不会退出事件循环，
        # 进程会在后台残留并继续占用数据库
        QApplication.instance().quit()

    def _on_session_end(self, manager):
        """注销/关机：不能阻止会话结束，按真正退出处理（后续 closeEvent 不再 ignore）。"""
        self._quitting = True
        self._shutdown(wait_ms=8_000)

    def closeEvent(self, event):
        if not self._quitting and self.tray is not None:
            # 最小化到托盘而不是退出
            event.ignore()
            self.hide()
            if not self._tray_notified:
                self._tray_notified = True
                show_toast("已最小化到托盘，右键托盘图标可退出")
            return
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self, wait_ms: int = 60_000):
        """统一退出清理（托盘退出 / 无托盘关窗 / 注销关机 / aboutToQuit 都会走到，只执行一次）。

        顺序：先置"正在退出"拒绝新收录 → 停 IPC（之后的转发回 busy）/ 监控 / 热键 →
        等写数据的 worker（收录/移除）结束；只读/联网的最多等 _READONLY_GRACE_SEC 就放弃。
        等待时每轮重新扫描 _workers：processEvents 期间可能冒出新 worker。
        不 terminate() 入库线程：强杀可能停在 SQLite 事务或 chroma 写入中途，损坏数据。
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._shutting_down = True
        self._ingest_queue.clear()
        for stop in (self.ipc_server.stop, self.watcher.stop, self.hotkey.stop):
            try:
                stop()
            except Exception:
                pass
        if self.tray is not None:
            self.tray.hide()
        start = time.monotonic()
        deadline = start + wait_ms / 1000
        notified = False
        while True:
            running = [w for w in list(self._workers) + list(_orphan_workers)
                       if _alive(w) and w.isRunning()]
            writers = [w for w in running if getattr(w, "WRITES_DATA", False)]
            readers = [w for w in running if not getattr(w, "WRITES_DATA", False)]
            if readers and time.monotonic() - start >= _READONLY_GRACE_SEC:
                # 只读/联网任务（问答、抓网页、验证 Key…）不值得让用户干等：断开回调后放弃，
                # 由进程退出回收线程
                for w in readers:
                    _detach_worker(w)
                readers = []
            busy = writers + readers
            if not busy:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 超时（入库卡在网络盘等）：记日志，由进程退出回收线程
                try:
                    import logging

                    logging.getLogger("pda").warning("退出时仍有 %d 个后台任务未结束", len(busy))
                    config.ensure_dirs()
                    with open(config.ERROR_LOG, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 退出时仍有 "
                            f"{len(busy)} 个后台任务未结束："
                            f"{', '.join(type(w).__name__ for w in busy)}\n"
                        )
                except Exception:
                    pass
                for w in busy:
                    _detach_worker(w)  # 脱离窗口，避免随窗口析构时 "QThread destroyed while running"
                break
            if not notified and writers:
                notified = True
                self.statusBar().showMessage("正在等待后台任务结束…")
                if self._ingest_running:
                    show_progress("正在完成收录，稍后退出…")
            # 分片等待 + 处理事件：界面不卡死成"未响应"，worker 的 finished 信号也能被处理
            busy[0].wait(int(min(remaining, 0.2) * 1000))
            QApplication.processEvents()


class HotkeyForm(QWidget):
    """全局快捷键录入区：每个动作一个按键录入框，可清空（不启用）或恢复默认。

    快捷键对话框和设置对话框共用；validity_changed 通知外层启用/禁用保存按钮。
    """

    validity_changed = Signal(bool)

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self._current = dict(current)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edits = {}
        for action, (default, desc) in config.HOTKEY_ACTIONS.items():
            edit = QKeySequenceEdit()
            edit.setMaximumSequenceLength(1)  # 只要一组组合键，不要 Emacs 式连按
            edit.setClearButtonEnabled(True)
            edit.setAccessibleName(desc)
            edit.setKeySequence(QKeySequence.fromString(current.get(action, ""), QKeySequence.PortableText))
            edit.setToolTip(f"默认：{hotkey.display(default)}；点右侧 × 清空表示不启用")
            edit.keySequenceChanged.connect(self.validate)
            self._edits[action] = edit
            form.addRow(desc, edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("dialogStatus")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset)
        row.addWidget(reset_btn, 0, Qt.AlignTop)
        layout.addLayout(row)
        self.validate()

    def values(self) -> dict:
        return {a: e.keySequence().toString(QKeySequence.PortableText) for a, e in self._edits.items()}

    def _reset(self):
        for action, (default, _) in config.HOTKEY_ACTIONS.items():
            self._edits[action].setKeySequence(QKeySequence.fromString(default, QKeySequence.PortableText))

    def problems(self) -> list:
        problems = []
        seen = {}
        for action, seq in self.values().items():
            if not seq:
                continue
            desc = config.HOTKEY_ACTIONS[action][1]
            _, _, err = hotkey.parse(seq)
            if err:
                problems.append(f"「{desc}」：{err}")
                continue
            if seq in seen:
                problems.append(f"「{desc}」和「{seen[seq]}」用了同一组按键")
                continue
            seen[seq] = desc
            # 本程序当前已注册的按键试注册会失败，不算占用
            if seq not in self._current.values() and not hotkey.is_available(seq):
                problems.append(f"「{desc}」的 {hotkey.display(seq)} 已被其他程序占用，请换一个")
        return problems

    def validate(self, *_) -> bool:
        problems = self.problems()
        if problems:
            self.status.setStyleSheet("color: #D14343; font-size: 12px;")
            self.status.setText("\n".join(problems))
        elif not any(self.values().values()):
            self.status.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
            self.status.setText("所有快捷键都已关闭，仍可拖放或 Ctrl+V 粘贴收录。")
        else:
            self.status.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
            self.status.setText("点击输入框后按下新的组合键即可修改，需要包含 Ctrl、Alt 或 Win 键。")
        self.validity_changed.emit(not problems)
        return not problems


class HotkeyDialog(QDialog):
    """全局快捷键设置（欢迎面板"修改"、侧栏"快捷键"按钮打开）。"""

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("全局快捷键")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        tip = QLabel("在任何程序里按下这些快捷键都能直接呼出界面或收录。")
        tip.setObjectName("dialogTip")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("保存")
        self.ok_btn.setObjectName("primaryButton")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(self.ok_btn)

        self.form = HotkeyForm(current, self)
        self.form.validity_changed.connect(self.ok_btn.setEnabled)
        self.ok_btn.setEnabled(not self.form.problems())
        layout.addWidget(self.form)
        layout.addLayout(bottom)

    def values(self) -> dict:
        return self.form.values()

    def accept(self):
        if not self.form.validate():  # 录入后外部程序才占用等极端情况：保存前再查一次
            return
        super().accept()


class WatchFoldersDialog(QDialog):
    """设置：监控文件夹、开机自启、AI 问答接口、全局快捷键；确定后由主窗口保存（监控变化时重启 watcher）。"""

    def __init__(self, current_hotkeys: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(480, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel("以下文件夹中的新文档会自动收录进知识库：")
        tip.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        layout.addWidget(tip)

        self.list = QListWidget()
        for folder in config.get_watch_folders():
            self.list.addItem(folder)
        layout.addWidget(self.list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("添加文件夹...")
        add_btn.clicked.connect(self._on_add)
        remove_btn = QPushButton("移除选中")
        remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.autostart_cb = QCheckBox("开机自动启动")
        self.autostart_cb.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        self.autostart_cb.setChecked(config.autostart_enabled())
        layout.addWidget(self.autostart_cb)

        # ---------- AI 问答：只填 Key，接口地址与模型自动确定 ----------
        llm_title = QLabel("AI 问答（可选；不填则只返回检索到的原文片段）：")
        llm_title.setWordWrap(True)
        llm_title.setStyleSheet(f"color: {TEXT}; font-size: 13px; margin-top: 6px;")
        layout.addWidget(llm_title)
        llm_cfg = config.get_llm_file_config()
        self._saved_llm = llm_cfg
        form = QFormLayout()
        form.setSpacing(6)
        self.provider_combo = QComboBox()
        self.provider_combo.setAccessibleName("服务商")
        for p in llm.PROVIDERS:
            self.provider_combo.addItem(p["name"], p["id"])
        self.provider_combo.addItem("其他（OpenAI 兼容接口）", llm.CUSTOM_PROVIDER)
        self.api_key_edit = QLineEdit(llm_cfg["api_key"])
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("粘贴 API Key")
        self.api_key_edit.setAccessibleName("API Key")
        # 跳到当前服务商的 Key 管理页；"其他"服务商没有固定地址，隐藏
        self.get_key_btn = QPushButton("获取 API Key")
        self.get_key_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.get_key_btn.clicked.connect(self._open_key_page)
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.get_key_btn)
        # 模型：下拉选择（自动 / 预设 / 账户实际可用列表），也可直接输入
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.setAccessibleName("模型")
        self.model_combo.setMinimumContentsLength(18)
        self.refresh_models_btn = QPushButton("获取可用模型")
        self.refresh_models_btn.setToolTip("用上面的 API Key 向该服务商查询你账户下可用的模型")
        self.refresh_models_btn.clicked.connect(self._fetch_models)
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.refresh_models_btn)
        form.addRow("服务商", self.provider_combo)
        form.addRow("API Key", key_row)
        form.addRow("模型", model_row)
        layout.addLayout(form)
        self.llm_status = QLabel()
        self.llm_status.setWordWrap(True)
        self.llm_status.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
        layout.addWidget(self.llm_status)

        # 高级：接口地址，默认收起（预设服务商无需填写）
        self.advanced_btn = QPushButton("高级设置 ▸")
        self.advanced_btn.setFlat(True)
        self.advanced_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.advanced_btn.setStyleSheet(f"color: {SUBTLE}; font-size: 12px; text-align: left; border: none;")
        self.advanced_btn.clicked.connect(lambda: self._set_advanced(not self.advanced_box.isVisible()))
        layout.addWidget(self.advanced_btn, 0, Qt.AlignLeft)
        self.advanced_box = QWidget()
        adv = QFormLayout(self.advanced_box)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(6)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setAccessibleName("接口地址")
        adv.addRow("接口地址", self.base_url_edit)
        layout.addWidget(self.advanced_box)

        self._verifying = False
        self._models_worker = None
        self._fetched_for = None  # (provider, key, base_url)：避免同一组合重复查询
        # 回显已保存的配置：接口地址是预设服务商的就选中它，否则算"其他"
        saved_pid = llm.provider_for_base_url(llm_cfg["base_url"] or config.DEFAULT_BASE_URL)
        if saved_pid is None:
            saved_pid = llm.CUSTOM_PROVIDER
            self.base_url_edit.setText(llm_cfg["base_url"])
        self._select_provider(saved_pid)
        # 自动模式：下拉框停在"自动"，状态行显示上次自动选定的模型
        current = llm_cfg["model"] if llm_cfg["api_key"] and not llm_cfg["model_auto"] else ""
        self._set_advanced(saved_pid == llm.CUSTOM_PROVIDER)
        self._on_provider_changed()
        self._set_model_text(current)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.api_key_edit.textEdited.connect(self._on_key_edited)
        # Key 填完（失焦/回车）自动拉取账户可用模型，用户不需要知道模型名
        self.api_key_edit.editingFinished.connect(lambda: self._fetch_models(auto=True))
        if llm_cfg["api_key"]:
            if llm_cfg["model_auto"]:
                picked = llm_cfg["model"]
                self._set_llm_status(f"已配置（自动使用最新模型{f'：{picked}' if picked else ''}）")
            else:
                self._set_llm_status(f"已配置（模型：{current}）")
        # 未改动时保存不再重新验证
        self._llm_initial = self._llm_inputs()
        self._llm_result = None
        self.auto_tags_cb = QCheckBox("收录时用 AI 自动生成标签（会发送文档前 1500 字）")
        self.auto_tags_cb.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        self.auto_tags_cb.setChecked(config.auto_tags_enabled())
        layout.addWidget(self.auto_tags_cb)
        overrides = config.llm_env_overrides()
        if overrides:
            env_tip = QLabel(f"注意：已设置环境变量 {', '.join(overrides)}，对应项以环境变量为准。")
            env_tip.setWordWrap(True)
            env_tip.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
            layout.addWidget(env_tip)
        privacy = QLabel("配置后，提问时检索到的片段会发送给该接口的服务商。")
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"color: {SUBTLE}; font-size: 12px;")
        layout.addWidget(privacy)

        # ---------- 全局快捷键：和 API Key 一样在这里配置 ----------
        hk_title = QLabel("全局快捷键（在任何程序里按下都能直接呼出界面或收录）：")
        hk_title.setWordWrap(True)
        hk_title.setStyleSheet(f"color: {TEXT}; font-size: 13px; margin-top: 6px;")
        layout.addWidget(hk_title)
        self.hotkey_form = HotkeyForm(current_hotkeys, self)
        layout.addWidget(self.hotkey_form)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("保存")
        self.ok_btn.setObjectName("primaryButton")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(self.ok_btn)
        layout.addLayout(bottom)

    def _on_add(self):
        folder = QFileDialog.getExistingDirectory(self, "选择要监控的文件夹")
        if folder:
            items = [self.list.item(i).text() for i in range(self.list.count())]
            if folder not in items:
                self.list.addItem(folder)

    def _on_remove(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def folders(self) -> list:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def autostart_checked(self) -> bool:
        return self.autostart_cb.isChecked()

    def hotkey_values(self) -> dict:
        return self.hotkey_form.values()

    def focus_api_key(self):
        """从欢迎面板"配置 API Key"卡片打开时，光标直接落在 API Key 输入框。"""
        # 对话框 exec() 显示后才能可靠地设置焦点
        def _focus():
            self.api_key_edit.setFocus(Qt.OtherFocusReason)
            self.api_key_edit.selectAll()

        QTimer.singleShot(0, _focus)

    # ----- AI 问答：服务商 / Key / 高级 -----

    def _select_provider(self, provider_id):
        idx = self.provider_combo.findData(provider_id)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)

    def _provider_id(self):
        return self.provider_combo.currentData()

    def _set_advanced(self, show: bool):
        self.advanced_box.setVisible(show)
        self.advanced_btn.setText("高级设置 ▾" if show else "高级设置 ▸")

    def _set_llm_status(self, text: str, error: bool = False):
        color = "#D14343" if error else SUBTLE
        self.llm_status.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.llm_status.setText(text)

    _AUTO_MODEL = "自动（始终用最新模型）"

    def _open_key_page(self):
        p = llm.get_provider(self._provider_id())
        if p and p.get("key_url"):
            QDesktopServices.openUrl(QUrl(p["key_url"]))

    def _on_provider_changed(self, *_):
        p = llm.get_provider(self._provider_id())
        key_url = p.get("key_url") if p else None
        self.get_key_btn.setVisible(bool(key_url))
        self.get_key_btn.setToolTip(f"在浏览器中打开 {p['name']} 的 API Key 管理页面\n{key_url}"
                                    if key_url else "")
        if p is not None:
            self.base_url_edit.setPlaceholderText(p["base_url"])
            self.base_url_edit.setEnabled(False)  # 预设服务商地址固定
            self.base_url_edit.clear()
        else:
            self.base_url_edit.setPlaceholderText("https://.../v1（必填）")
            self.base_url_edit.setEnabled(True)
            self._set_advanced(True)
        # 换服务商：先列预设模型，拿到 Key 后再换成账户实际可用列表
        self._fill_models(p["models"] if p else [], keep=False)
        self._fetched_for = None
        # 打开对话框时不自动联网；用户改了服务商且已有 Key 时才自动查询
        if getattr(self, "_llm_initial", None) is not None and self._provider_ready():
            self._fetch_models(auto=True)

    def _fill_models(self, models, keep=True):
        """重填模型下拉：第一项"自动选择"，其后是模型名。keep=True 时保留当前选择/输入。"""
        prev = self._model_text() if keep else ""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem(self._AUTO_MODEL, "")
        for m in models:
            self.model_combo.addItem(m, m)
        self.model_combo.blockSignals(False)
        self._set_model_text(prev)

    def _set_model_text(self, model: str):
        if not model:
            self.model_combo.setCurrentIndex(0)
            return
        idx = self.model_combo.findData(model)
        if idx < 0:  # 已保存/手动输入的模型不在列表里：补上，保证回显
            self.model_combo.addItem(model, model)
            idx = self.model_combo.count() - 1
        self.model_combo.setCurrentIndex(idx)

    def _model_text(self) -> str:
        text = self.model_combo.currentText().strip()
        return "" if text in ("", self._AUTO_MODEL) else text

    def _provider_ready(self) -> bool:
        return bool(self.api_key_edit.text().strip()) and (
            llm.get_provider(self._provider_id()) is not None or bool(self.base_url_edit.text().strip())
        )

    def _endpoint(self):
        p = llm.get_provider(self._provider_id())
        if p is not None:
            return p["base_url"], p["models"]
        return self.base_url_edit.text().strip(), []

    def _fetch_models(self, auto: bool = False):
        """用当前 Key 查询账户可用模型并填进下拉框（后台线程）。auto=True 时静默、同组合只查一次。"""
        if self._models_worker is not None or self._verifying:
            return
        if not self._provider_ready():
            if not auto:
                self._set_llm_status("先填写 API Key（「其他」服务商还需填写接口地址）", error=True)
            return
        key = self.api_key_edit.text().strip()
        base_url, prefs = self._endpoint()
        combo = (self._provider_id(), key, base_url)
        if auto and combo == self._fetched_for:
            return
        self._fetched_for = combo
        self.refresh_models_btn.setEnabled(False)
        self.refresh_models_btn.setText("获取中…")
        worker = _ModelsWorker(key, base_url, prefs, parent=self)
        worker.done.connect(lambda ok, models, msg: self._on_models(ok, models, msg, auto))
        worker.finished.connect(worker.deleteLater)
        self._models_worker = worker
        _track_side_worker(worker)  # 主窗口退出时也要处理到（见 MainWindow._shutdown）
        worker.start()

    def _on_models(self, ok, models, msg, auto):
        self._models_worker = None
        self.refresh_models_btn.setEnabled(True)
        self.refresh_models_btn.setText("获取可用模型")
        if not ok:
            self._fetched_for = None  # 失败允许重试
            if not auto:
                self._set_llm_status(msg, error=True)
            return
        if models:
            self._fill_models(models)
            self._set_llm_status(
                f"已获取 {len(models)} 个可用模型，最新的是 {models[0]}；"
                "选「自动」会一直跟随最新模型，也可以固定一个"
            )
        elif not auto:
            self._set_llm_status("该服务商不提供模型列表，可从下拉框选预设模型或直接输入模型名")

    def _on_key_edited(self, text: str):
        """粘贴 Key 时按格式自动选中服务商（只认前缀独特的几家，其余保持用户选择）。"""
        guessed = llm.guess_provider(text)
        if guessed:
            self._select_provider(guessed)

    def _llm_inputs(self) -> tuple:
        return (
            self._provider_id(),
            self.api_key_edit.text().strip(),
            self.base_url_edit.text().strip(),
            self._model_text(),
        )

    def accept(self):
        """保存前验证 Key：只向所选服务商发一次请求，同时自动选定模型。验证在后台线程，不卡界面。"""
        if self._verifying:
            return
        if not self.hotkey_form.validate():  # 快捷键无效/冲突：提示显示在快捷键区，不关闭
            return
        if self._models_worker is not None:
            # 点"保存"时 Key 输入框失焦会自动触发查询模型列表；以前在这里直接 return，
            # 保存被静默拒绝、用户关窗后 Key 丢失。查询只是为了填下拉框，下面的验证
            # 会自己选定模型：放弃这次查询，继续保存
            _detach_worker(self._models_worker)
            self._models_worker = None
            self._fetched_for = None
            self.refresh_models_btn.setEnabled(True)
            self.refresh_models_btn.setText("获取可用模型")
        inputs = self._llm_inputs()
        pid, key, base_url, model = inputs
        if inputs == self._llm_initial:
            self._llm_result = None  # AI 设置没动：不重新验证、不改配置
            super().accept()
            return
        if not key:
            self._llm_result = {"api_key": "", "base_url": "", "model": "",
                                "model_auto": True}  # 清空 = 关闭智能回答
            super().accept()
            return
        p = llm.get_provider(pid)
        if p is None:
            if not base_url:
                self._set_advanced(True)
                self._set_llm_status("「其他」服务商需要在高级设置里填写接口地址", error=True)
                self.base_url_edit.setFocus()
                return
            prefs = []
        else:
            base_url, prefs = p["base_url"], p["models"]
        self._verifying = True
        self.ok_btn.setEnabled(False)
        self._set_llm_status("正在验证 API Key…")
        worker = _VerifyWorker(key, base_url, prefs, model, parent=self)
        worker.done.connect(lambda ok, value, offline: self._on_verified(ok, value, offline, key, base_url, model))
        worker.finished.connect(worker.deleteLater)
        self._verify_worker = worker  # 持有引用直到结束
        _track_side_worker(worker)
        worker.start()

    def _on_verified(self, ok, value, offline, key, base_url, model):
        self._verifying = False
        self.ok_btn.setEnabled(True)
        if ok:
            # 没指定模型 = 自动：value 只是本次选出的最新模型，之后每次启动会重新选
            self._llm_result = {"api_key": key, "base_url": base_url, "model": value,
                                "model_auto": not model}
            self._set_llm_status(f"验证通过，使用模型 {value}")
            super().accept()
            return
        if offline:
            reply = QMessageBox.question(
                self, "无法验证",
                f"{value}。\n\n仍然保存吗？联网后提问时才能确认 Key 是否可用。",
            )
            if reply == QMessageBox.Yes:
                p = llm.get_provider(self._provider_id())
                fallback = model or (p["models"][0] if p else "")
                self._llm_result = {"api_key": key, "base_url": base_url, "model": fallback,
                                    "model_auto": not model}
                super().accept()
                return
        self._set_llm_status(value, error=True)

    def reject(self):
        if self._verifying:
            # 验证中不关闭（避免后台线程回调到已销毁的对话框），但要让用户知道为什么没反应
            self._set_llm_status("正在验证 API Key，请稍候…（网络不通时最多约 12 秒）")
            return
        if self._models_worker is not None:
            # 取消时模型列表还在查：断开回调、脱离父对象让它自行结束，不阻塞关闭
            _detach_worker(self._models_worker)
            self._models_worker = None
        super().reject()

    def llm_values(self):
        """验证通过后的 {api_key, base_url, model, auto_tags}；AI 设置未改动时 api_key 等为 None。"""
        values = dict(self._llm_result or {"api_key": None, "base_url": None, "model": None,
                                           "model_auto": None})
        values["auto_tags"] = self.auto_tags_cb.isChecked()
        return values


class _ModelsWorker(QThread):
    done = Signal(bool, list, str)  # ok, 模型列表, 错误信息

    def __init__(self, key, base_url, prefs, parent=None):
        super().__init__(parent)
        self._args = (key, base_url, prefs)

    def run(self):
        try:
            self.done.emit(True, llm.list_chat_models(*self._args), "")
        except llm.LlmSetupError as e:
            self.done.emit(False, [], str(e))
        except Exception as e:
            self.done.emit(False, [], f"获取模型列表失败：{e}")


class _VerifyWorker(QThread):
    done = Signal(bool, str, bool)  # ok, 模型名或错误信息, 是否网络问题

    def __init__(self, key, base_url, prefs, model, parent=None):
        super().__init__(parent)
        self._args = (key, base_url, prefs, model)

    def run(self):
        key, base_url, prefs, model = self._args
        try:
            self.done.emit(True, llm.verify_and_pick_model(key, base_url, prefs, model), False)
        except llm.LlmSetupError as e:
            self.done.emit(False, str(e), e.offline)
        except Exception as e:  # 兜底：任何异常都要让界面恢复可用
            self.done.emit(False, f"验证失败：{e}", False)
