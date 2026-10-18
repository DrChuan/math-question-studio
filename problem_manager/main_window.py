from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import shutil
from uuid import uuid4
from urllib.parse import unquote

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRunnable, QSettings, QSignalBlocker, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from problem_manager.export_dialog import ExportOptionsDialog
from problem_manager.drawing_canvas import DrawingCanvas
from problem_manager.backup import (
    BackupError,
    apply_restored_backup,
    create_backup,
    discard_restored_backup,
    read_backup,
)
from problem_manager.latex_exporter import LatexExportError, export_questions_to_latex_bundle
from problem_manager.mathtype_support import detect_mathtype
from problem_manager.math_normalizer import normalize_mixed_latex_text, migrate_blank_markers
from problem_manager.models import CHOICE_TYPES, OPTION_LABELS, Question, QuestionBasket, QuestionType
from problem_manager.preview import (
    render_browse_cards,
    render_overview,
    render_question_preview,
    render_question_preview_fragment,
    render_text_editor_preview,
    render_text_editor_preview_fragment,
    _render_question_image,
    configure_formula_cache,
)
from problem_manager.repository import QuestionRepository
from problem_manager.tag_catalog import DEFAULT_METHOD_TAGS, KNOWLEDGE_TAG_TREE, flatten_knowledge_tags


class BrowseActionPage(QWebEnginePage):
    """Keeps card controls in HTML while routing their actions back into Qt."""

    action_requested = Signal(str, str)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # type: ignore[override]
        if url.scheme() == "pm":
            self.action_requested.emit(url.host(), url.path().lstrip("/"))
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class PreviewSourcePage(QWebEnginePage):
    """Receives Command-click source locations from a preview document."""

    source_location_requested = Signal(str, int)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id) -> None:  # type: ignore[override]
        prefix = "pm-edit-location:"
        if message.startswith(prefix):
            target, separator, offset = message[len(prefix) :].partition(":")
            if separator and offset.isdigit():
                self.source_location_requested.emit(target, int(offset))
                return
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class PreviewRenderSignals(QObject):
    """Delivers a background LaTeX render result back to the GUI thread."""

    finished = Signal(int, bool, str)


class PreviewRenderTask(QRunnable):
    """Keeps local TeX subprocess work off the Qt event loop."""

    def __init__(self, generation: int, full_document: bool, renderer) -> None:
        super().__init__()
        self.generation = generation
        self.full_document = full_document
        self.renderer = renderer
        self.signals = PreviewRenderSignals()

    def run(self) -> None:
        try:
            rendered = self.renderer()
        except Exception:
            rendered = ""
        try:
            self.signals.finished.emit(self.generation, self.full_document, rendered)
        except RuntimeError:
            # The editor may have closed while a TeX subprocess was finishing.
            pass


PREVIEW_RENDER_POOL = QThreadPool()
PREVIEW_RENDER_POOL.setMaxThreadCount(1)


def _replace_preview_content_script(fragment: str, scroll_y: int) -> str:
    """Replace the preview body without navigating the QWebEngine page."""
    content = json.dumps(fragment, ensure_ascii=False)
    position = max(0, int(scroll_y))
    return f"""
(() => {{
  const container = document.getElementById('preview-content');
  if (!container) return false;
  container.innerHTML = {content};
  window.scrollTo(0, {position});
  requestAnimationFrame(() => window.scrollTo(0, {position}));
  return true;
}})();
"""


class RefinedComboBox(QComboBox):
    """Keeps a visible, consistent arrow on macOS and Qt's themed widgets."""

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor("#315d50"))
        font = painter.font()
        font.setPointSize(max(11, font.pointSize()))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.width() - 30, 0, 30, self.height(), Qt.AlignCenter, "▾")
        painter.end()


class ImagePasteEditor(QPlainTextEdit):
    image_pasted = Signal(QImage)

    def insertFromMimeData(self, source) -> None:
        if source.hasImage():
            self.image_pasted.emit(QImage(source.imageData()))
        elif source.hasUrls() and all(url.isLocalFile() and not QImage(url.toLocalFile()).isNull() for url in source.urls()):
            for url in source.urls():
                self.image_pasted.emit(QImage(url.toLocalFile()))
        else:
            super().insertFromMimeData(source)


class LatexTextEditorDialog(QDialog):
    """Expanded editor with a dedicated offline LaTeX preview."""

    def __init__(self, title: str, text: str, parent=None, image_paths: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑{title}")
        self.resize(1240, 760)
        layout = QVBoxLayout(self)
        hint = QLabel("左侧输入文本与 $...$ 行内公式；右侧实时显示本地 LaTeX 渲染。按住 Command 点击预览可定位到原文。")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        splitter = QSplitter(Qt.Horizontal)
        self.editor = ImagePasteEditor(text)
        self.attached_images: list[QImage] = []
        self.image_paths = list(image_paths or [])
        self.editor.image_pasted.connect(self._attach_image)
        self.editor.setPlaceholderText(f"请输入{title}，支持 $...$ 形式的 LaTeX")
        self.editor.setMinimumWidth(460)
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        splitter.addWidget(self.editor)
        self.preview = QWebEngineView()
        self.preview_page = PreviewSourcePage(self.preview)
        self.preview_page.source_location_requested.connect(self._focus_source_location)
        self.preview.setPage(self.preview_page)
        self.preview.setMinimumWidth(360)
        splitter.addWidget(self.preview)
        splitter.setChildrenCollapsible(False)
        # Editing remains the primary task; preview keeps a readable but bounded column.
        splitter.setSizes([760, 450])
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        buttons = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        confirm_button = QPushButton("写回输入框")
        confirm_button.setObjectName("primaryButton")
        confirm_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(confirm_button)
        layout.addLayout(buttons)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(520)
        self._pending_preview_scroll_y: int | None = None
        self._preview_render_generation = 0
        self._preview_loaded = False
        self._preview_tasks: dict[int, PreviewRenderTask] = {}
        self._preview_timer.timeout.connect(self._request_preview_render)
        self.editor.textChanged.connect(self._preview_timer.start)
        self.preview.loadFinished.connect(self._restore_preview_scroll_position)
        self._set_preview_html(0)

    def _attach_image(self, image: QImage) -> None:
        if not image.isNull():
            self.attached_images.append(image)
            self.setWindowTitle(self.windowTitle().split(" · ")[0] + f" · 已粘贴 {len(self.attached_images)} 张图片")
            self._preview_timer.start()

    def text(self) -> str:
        return self.editor.toPlainText()

    def _focus_source_location(self, target: str, offset: int) -> None:
        if target != "dialog":
            return
        cursor = self.editor.textCursor()
        cursor.setPosition(min(max(0, offset), len(self.editor.toPlainText())))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        self.editor.ensureCursorVisible()

    def _request_preview_render(self) -> None:
        if not self._preview_loaded:
            self._set_preview_html(0)
            return
        self.preview.page().runJavaScript(
            "window.scrollY || 0",
            self._set_preview_html,
        )

    def _set_preview_html(self, scroll_y) -> None:
        try:
            self._pending_preview_scroll_y = max(0, int(scroll_y or 0))
        except (TypeError, ValueError):
            self._pending_preview_scroll_y = 0
        self._preview_render_generation += 1
        title = self.windowTitle().removeprefix("编辑")
        generation = self._preview_render_generation
        text = self.editor.toPlainText()
        full_document = not self._preview_loaded
        image_html = "".join(_render_question_image(path, "preview-image") for path in self.image_paths)
        for image in self.attached_images:
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            image_html += "<p><img style='max-width:100%;max-height:340px' src='data:image/png;base64," + bytes(data.toBase64()).decode("ascii") + "'></p>"
        renderer = (
            lambda: render_text_editor_preview(text, title).replace("</article>", image_html + "</article>")
            if full_document
            else render_text_editor_preview_fragment(text, title) + image_html
        )
        task = PreviewRenderTask(generation, full_document, renderer)
        task.signals.finished.connect(self._apply_preview_render)
        self._preview_tasks[generation] = task
        PREVIEW_RENDER_POOL.start(task)

    def _apply_preview_render(self, generation: int, full_document: bool, rendered: str) -> None:
        self._preview_tasks.pop(generation, None)
        if generation != self._preview_render_generation or not rendered:
            return
        if full_document:
            self.preview.setHtml(rendered, QUrl("https://preview.local/"))
            return
        self.preview.page().runJavaScript(
            _replace_preview_content_script(rendered, self._pending_preview_scroll_y or 0),
        )

    def _restore_preview_scroll_position(self, loaded: bool) -> None:
        if not loaded:
            return
        self._preview_loaded = True
        if self._pending_preview_scroll_y is None:
            return
        position = self._pending_preview_scroll_y
        generation = self._preview_render_generation

        def restore() -> None:
            if generation == self._preview_render_generation:
                self.preview.page().runJavaScript(f"window.scrollTo(0, {position});")

        restore()
        QTimer.singleShot(260, restore)


class DrawingDialog(QDialog):
    """Creates a clean PNG illustration that can be attached to the current question."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("绘制题目配图")
        self.resize(1160, 820)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("工具"))
        self.tool_combo = RefinedComboBox()
        self.tool_combo.addItems(["画笔", "直线", "矩形", "圆/椭圆", "文字"])
        tools.addWidget(self.tool_combo)
        tools.addWidget(QLabel("线宽"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 12)
        self.width_spin.setValue(3)
        tools.addWidget(self.width_spin)
        self.color_button = QPushButton("颜色")
        tools.addWidget(self.color_button)
        undo_button = QPushButton("撤销")
        clear_button = QPushButton("清空")
        tools.addWidget(undo_button)
        tools.addWidget(clear_button)
        tools.addStretch(1)
        layout.addLayout(tools)
        self.canvas = DrawingCanvas()
        layout.addWidget(self.canvas, 1)
        buttons = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        use_button = QPushButton("作为题目配图")
        use_button.setObjectName("primaryButton")
        use_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(use_button)
        layout.addLayout(buttons)
        self.tool_combo.currentTextChanged.connect(self.canvas.set_tool)
        self.width_spin.valueChanged.connect(self.canvas.set_pen_width)
        self.color_button.clicked.connect(self._choose_color)
        undo_button.clicked.connect(self.canvas.undo)
        clear_button.clicked.connect(self.canvas.clear)

    def image(self) -> QImage:
        return self.canvas.export_image()

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.color, self, "选择画笔颜色")
        if color.isValid():
            self.canvas.set_color(color)
            self.color_button.setStyleSheet(f"background: {color.name()}; color: {'white' if color.lightness() < 128 else '#18251f'};")


class ImageDropLabel(QLabel):
    """Small drop target for either an image file or an image dragged from another app."""

    image_file_dropped = Signal(str)
    image_dropped = Signal(object)
    paste_requested = Signal()

    def __init__(self) -> None:
        super().__init__("将图片文件拖到这里，或单击后使用粘贴快捷键")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(150)
        self.setObjectName("imageDropTarget")
        self.setStyleSheet("border: 1px dashed #a3b8a8; background: #f5f8f3; color: #66776a;")

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        mime_data = event.mimeData()
        if mime_data.hasImage() or mime_data.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        mime_data = event.mimeData()
        for url in mime_data.urls():
            if url.isLocalFile():
                self.image_file_dropped.emit(url.toLocalFile())
                event.acceptProposedAction()
                return
        image = mime_data.imageData()
        if isinstance(image, QImage) and not image.isNull():
            self.image_dropped.emit(image)
            event.acceptProposedAction()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.Paste):
            self.paste_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, repository: QuestionRepository, data_file: Path) -> None:
        super().__init__()
        self.repository = repository
        self.data_file = data_file
        configure_formula_cache(self.data_file.parent / ".formula-cache")
        self.image_directory = self.data_file.parent / "images"
        self.image_directory.mkdir(parents=True, exist_ok=True)
        self.draft_image_directory = self.data_file.parent / ".draft-images"
        self.draft_image_directory.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings("DrChuan", "Math Question Studio")
        self.state = repository.load()
        self.questions = self.state.questions
        self.method_tag_library = self._merge_method_tags(self.state.method_tag_library)
        self.learning_tag_library = self._unique_tags(self.state.learning_tag_library)
        self.baskets = self._valid_baskets(self.state.baskets)
        self.active_basket_id = self.state.active_basket_id if self.state.active_basket_id else self.baskets[0].id

        self.current_question_id: str | None = None
        self.editing_question_id: str | None = None
        self._is_new_draft = False
        self.editing_image_path = ""
        self.editing_image_paths: list[str] = []
        self.editing_analysis_images: list[str] = []
        self._original_image_path = ""
        self._editor_baseline: dict | None = None
        self._text_editor_dialogs: set[LatexTextEditorDialog] = set()
        self._loading_question = False
        self._changing_browse_tags = False
        self.expanded_question_ids: set[str] = set()
        self.math_type_status = detect_mathtype()
        self.current_mode = "browse"
        self._pending_browse_scroll_y: int | None = None
        self._browse_render_generation = 0
        self._browse_render_tasks: dict[int, PreviewRenderTask] = {}
        self._browse_loading = False
        self._browse_sidebar_sizes: list[int] | None = None
        self._pending_edit_preview_scroll_y: int | None = None
        self._edit_preview_render_generation = 0
        self._edit_preview_loaded = False
        self._edit_preview_tasks: dict[int, PreviewRenderTask] = {}
        self._edit_preview_timer = QTimer(self)
        self._edit_preview_timer.setSingleShot(True)
        self._edit_preview_timer.setInterval(520)
        self._edit_preview_timer.timeout.connect(self._request_edit_preview_render)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._refresh_browse_results)

        self.setWindowTitle("高中数学题目管理器")
        if not self.restoreGeometry(self.settings.value("windowGeometry", b"")):
            self.resize(1680, 980)
        self._build_menu()
        self._build_ui()
        changed = False
        for question in self.questions:
            if question.question_type == QuestionType.FILL_BLANK.value:
                migrated = migrate_blank_markers(question.stem)
                if migrated != question.stem:
                    if not changed and self.data_file.exists():
                        shutil.copy2(self.data_file, self.data_file.with_name("questions-before-blank-migration.json"))
                    question.stem = migrated
                    changed = True
        if changed:
            self.repository.save(self.state)
        self._populate_browse_tag_tree()
        self._populate_browse_method_filter_list()
        self._populate_browse_learning_filter_list()
        self._populate_edit_knowledge_tree()
        self._populate_method_tag_list([])
        self._populate_learning_tag_list([])
        self._restore_splitter_state()
        self.browse_count_label.setText("正在准备题目…")
        QTimer.singleShot(0, self._refresh_browse_results)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        new_action = QAction("新建题目", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.add_question)
        file_menu.addAction(new_action)
        overview_action = QAction("题库概览", self)
        overview_action.triggered.connect(self.open_overview)
        file_menu.addAction(overview_action)
        file_menu.addSeparator()
        backup_action = QAction("备份题库…", self)
        backup_action.triggered.connect(self.backup_library)
        restore_action = QAction("恢复题库…", self)
        restore_action.triggered.connect(self.restore_library)
        file_menu.addAction(backup_action)
        file_menu.addAction(restore_action)

        basket_menu = self.menuBar().addMenu("试题篮")
        view_basket_action = QAction("查看试题篮", self)
        view_basket_action.triggered.connect(self.open_basket)
        export_basket_action = QAction("导出试题篮", self)
        export_basket_action.triggered.connect(self.export_basket_questions)
        basket_menu.addAction(view_basket_action)
        basket_menu.addAction(export_basket_action)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)

        self.root_splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(self.root_splitter)
        self.browse_sidebar = self._build_left_panel()
        self.root_splitter.addWidget(self.browse_sidebar)
        self.root_splitter.addWidget(self._build_workspace_panel())
        self.root_splitter.setSizes([330, 1330])

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        title = QLabel("标签检索")
        title.setObjectName("sidebarTitle")
        layout.addWidget(title)
        hint = QLabel("勾选多个章节或知识点时取并集；与方法/思想筛选同时使用时取交集。")
        hint.setObjectName("mutedText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        helper_row = QHBoxLayout()
        clear_button = QPushButton("清空标签")
        clear_button.clicked.connect(self._clear_browse_tag_filters)
        expand_button = QPushButton("展开全部")
        expand_button.clicked.connect(self._expand_all_browse_tags)
        helper_row.addWidget(clear_button)
        helper_row.addWidget(expand_button)
        layout.addLayout(helper_row)

        knowledge_label = QLabel("知识点（按教材章节）")
        knowledge_label.setObjectName("sectionLabel")
        layout.addWidget(knowledge_label)
        self.browse_tag_tree = QTreeWidget()
        self.browse_tag_tree.setHeaderHidden(True)
        self.browse_tag_tree.itemChanged.connect(self._on_browse_knowledge_item_changed)
        layout.addWidget(self.browse_tag_tree, 3)

        method_label = QLabel("方法 / 思想")
        method_label.setObjectName("sectionLabel")
        layout.addWidget(method_label)
        method_actions = QHBoxLayout()
        for title, action in [("新增", self.add_method_tag), ("编辑", self.rename_method_tag), ("删除", self.delete_method_tag)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            method_actions.addWidget(button)
        layout.addLayout(method_actions)
        self.browse_method_search = QLineEdit()
        self.browse_method_search.setPlaceholderText("搜索方法 / 思想")
        self.browse_method_search.textChanged.connect(self._filter_method_tags)
        layout.addWidget(self.browse_method_search)
        self.browse_method_list = QListWidget()
        self.browse_method_list.itemChanged.connect(self._on_browse_filter_changed)
        layout.addWidget(self.browse_method_list, 2)

        learning_label = QLabel("学习标签")
        learning_label.setObjectName("sectionLabel")
        layout.addWidget(learning_label)
        learning_row = QHBoxLayout()
        add_learning_button = QPushButton("新增")
        add_learning_button.clicked.connect(self.add_learning_tag)
        rename_learning_button = QPushButton("重命名")
        rename_learning_button.clicked.connect(self.rename_learning_tag)
        delete_learning_button = QPushButton("删除")
        delete_learning_button.clicked.connect(self.delete_learning_tag)
        learning_row.addWidget(add_learning_button)
        learning_row.addWidget(rename_learning_button)
        learning_row.addWidget(delete_learning_button)
        layout.addLayout(learning_row)
        self.browse_learning_list = QListWidget()
        self.browse_learning_list.setMaximumHeight(180)
        self.browse_learning_list.itemChanged.connect(self._on_browse_filter_changed)
        layout.addWidget(self.browse_learning_list, 1)
        return panel

    def _build_workspace_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(self._build_browse_page())
        self.workspace_stack.addWidget(self._build_edit_page())
        self.workspace_stack.addWidget(self._build_overview_page())
        layout.addWidget(self.workspace_stack)
        return panel

    def _build_overview_page(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("题库概览")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        return_button = QPushButton("返回题库")
        return_button.clicked.connect(self.open_browse)
        header.addWidget(return_button)
        layout.addLayout(header)
        self.overview_web = QWebEngineView()
        self.overview_web_page = BrowseActionPage(self.overview_web)
        self.overview_web_page.action_requested.connect(self._handle_overview_action)
        self.overview_web.setPage(self.overview_web_page)
        layout.addWidget(self.overview_web, 1)
        return panel

    def _build_browse_page(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索题干 / 答案 / 解析 / 备注 / 标签")
        self.search_edit.textChanged.connect(self._schedule_search_refresh)
        controls.addWidget(self.search_edit, 1)

        self.question_type_filter_combo = RefinedComboBox()
        self.question_type_filter_combo.addItem("全部题型", None)
        for item in QuestionType:
            self.question_type_filter_combo.addItem(item.value, item.value)
        self.question_type_filter_combo.currentIndexChanged.connect(self._on_browse_filter_changed)
        controls.addWidget(self.question_type_filter_combo)

        self.active_basket_combo = RefinedComboBox()
        self.active_basket_combo.setObjectName("basketCombo")
        self.active_basket_combo.setMinimumWidth(180)
        self.active_basket_combo.currentIndexChanged.connect(self._switch_basket_from_toolbar)
        controls.addWidget(self.active_basket_combo)
        self.manage_basket_button = QPushButton("管理试题篮")
        self.manage_basket_button.setObjectName("basketButton")
        self.manage_basket_button.clicked.connect(self.open_basket)
        controls.addWidget(self.manage_basket_button)
        self.export_basket_button = QPushButton("导出当前篮子")
        self.export_basket_button.setObjectName("primaryButton")
        self.export_basket_button.clicked.connect(self.export_basket_questions)
        controls.addWidget(self.export_basket_button)
        layout.addLayout(controls)

        browse_info = QHBoxLayout()
        self.browse_count_label = QLabel()
        self.browse_count_label.setObjectName("browseCount")
        browse_info.addWidget(self.browse_count_label)
        self.active_filter_label = QLabel()
        self.active_filter_label.setObjectName("activeFilters")
        self.active_filter_label.setWordWrap(True)
        browse_info.addWidget(self.active_filter_label, 1)
        layout.addLayout(browse_info)
        self.browse_web = QWebEngineView()
        self.browse_web_page = BrowseActionPage(self.browse_web)
        self.browse_web_page.action_requested.connect(self._handle_browse_card_action)
        self.browse_web.loadFinished.connect(self._restore_browse_scroll_position)
        self.browse_web.setPage(self.browse_web_page)
        layout.addWidget(self.browse_web, 1)
        return panel

    def _build_edit_page(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.edit_header_label = QLabel("编辑题目")
        self.edit_header_label.setObjectName("pageTitle")
        header.addWidget(self.edit_header_label)
        header.addStretch(1)
        cancel_button = QPushButton("取消并返回")
        cancel_button.clicked.connect(self.cancel_edit)
        save_return_button = QPushButton("保存并返回浏览")
        save_return_button.setObjectName("primaryButton")
        save_return_button.clicked.connect(self.save_and_return_to_browse)
        header.addWidget(cancel_button)
        self.preview_toggle_button = QPushButton("隐藏预览")
        self.preview_toggle_button.clicked.connect(self._toggle_edit_preview)
        header.addWidget(self.preview_toggle_button)
        header.addWidget(save_return_button)
        layout.addLayout(header)

        self.edit_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.edit_splitter, 1)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Do not let the scroll area be squeezed below a usable writing width.
        editor_scroll.setMinimumWidth(560)
        editor_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        editor_container = QWidget()
        editor_container.setMinimumWidth(540)
        editor_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(12, 12, 12, 18)
        editor_layout.setSpacing(10)

        metadata_group = QGroupBox("题目信息")
        metadata_layout = QGridLayout(metadata_group)
        metadata_layout.setHorizontalSpacing(10)
        metadata_layout.setVerticalSpacing(8)
        self.question_type_combo = RefinedComboBox()
        self.question_type_combo.addItems([item.value for item in QuestionType])
        self.question_type_combo.setMinimumWidth(128)
        self.question_type_combo.setMaximumWidth(150)
        self.question_type_combo.currentTextChanged.connect(self._on_editor_changed)
        metadata_layout.addWidget(QLabel("题型"), 0, 0)
        metadata_layout.addWidget(self.question_type_combo, 0, 1)

        self.difficulty_combo = RefinedComboBox()
        for value in range(1, 6):
            self.difficulty_combo.addItem(str(value), value)
        self.difficulty_combo.setMinimumWidth(78)
        self.difficulty_combo.setMaximumWidth(92)
        self.difficulty_combo.currentIndexChanged.connect(self._on_editor_changed)
        metadata_layout.addWidget(QLabel("难度"), 0, 2)
        metadata_layout.addWidget(self.difficulty_combo, 0, 3)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("可留空，例如：2025 年高考全国卷")
        self.source_edit.textChanged.connect(self._on_editor_changed)
        self.source_edit.setMinimumWidth(130)
        metadata_layout.addWidget(QLabel("来源"), 0, 4)
        metadata_layout.addWidget(self.source_edit, 0, 5)

        self.created_at_label = QLabel()
        self.created_at_label.setObjectName("mutedText")
        metadata_layout.addWidget(QLabel("创建日期"), 1, 0)
        metadata_layout.addWidget(self.created_at_label, 1, 1, 1, 5)
        metadata_layout.setColumnStretch(5, 1)
        editor_layout.addWidget(metadata_group)

        self.stem_edit = self._make_editor("支持 $...$；向量用 \\v{a_1} / \\v{AB}；填空横线用 @")
        self.stem_edit.setMinimumHeight(150)
        self.stem_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "题目", self.stem_edit)

        self.options_group = QGroupBox("选项")
        options_layout = QFormLayout(self.options_group)
        options_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.option_edits: list[QLineEdit] = []
        for label in OPTION_LABELS:
            editor = QLineEdit()
            editor.setPlaceholderText(f"{label} 选项（支持 LaTeX）")
            editor.setMinimumWidth(0)
            editor.textChanged.connect(self._on_editor_changed)
            options_layout.addRow(f"{label}", editor)
            self.option_edits.append(editor)
        editor_layout.addWidget(self.options_group)

        self.answer_edit = self._make_editor("答案支持 LaTeX")
        self.answer_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "答案", self.answer_edit)
        self.analysis_edit = self._make_editor("解析支持 LaTeX")
        self.analysis_edit.textChanged.connect(self._on_editor_changed)
        self.analysis_edit.image_pasted.connect(lambda image: self._store_qimage(image, analysis=True))
        self._add_editor_section(editor_layout, "解析", self.analysis_edit)
        self.analysis_images_list = QListWidget()
        self.analysis_images_list.setMaximumHeight(110)
        editor_layout.addWidget(self.analysis_images_list)
        analysis_image_row = QHBoxLayout()
        paste_analysis = QPushButton("粘贴解析图片")
        paste_analysis.clicked.connect(lambda: self._store_qimage(QApplication.clipboard().image(), analysis=True))
        remove_analysis = QPushButton("移除选中解析图片")
        remove_analysis.clicked.connect(lambda: self._remove_gallery_image(True))
        analysis_image_row.addWidget(paste_analysis)
        analysis_image_row.addWidget(remove_analysis)
        analysis_image_row.addStretch()
        editor_layout.addLayout(analysis_image_row)
        self.note_edit = self._make_editor("备注")
        self.note_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "备注", self.note_edit)

        self.tags_collapsed = False
        self.tag_section_toggle = QPushButton("收起标签区")
        self.tag_section_toggle.clicked.connect(self._toggle_tag_sections)
        editor_layout.addWidget(self.tag_section_toggle)

        knowledge_group = QGroupBox("知识点标签")
        knowledge_layout = QVBoxLayout(knowledge_group)
        self.edit_knowledge_tree = QTreeWidget()
        self.edit_knowledge_tree.setHeaderHidden(True)
        self.edit_knowledge_tree.setMinimumHeight(300)
        self.edit_knowledge_tree.itemChanged.connect(self._on_editor_changed)
        knowledge_layout.addWidget(self.edit_knowledge_tree)
        editor_layout.addWidget(knowledge_group)

        method_group = QGroupBox("方法/思想标签")
        method_layout = QVBoxLayout(method_group)
        method_row = QHBoxLayout()
        add_method_button = QPushButton("新增方法标签")
        add_method_button.clicked.connect(self.add_method_tag)
        method_row.addWidget(add_method_button)
        for title, action in [("编辑标签", self.rename_method_tag), ("删除标签", self.delete_method_tag)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            method_row.addWidget(button)
        method_row.addStretch(1)
        method_layout.addLayout(method_row)
        self.method_tag_list = QListWidget()
        self.method_tag_list.setMinimumHeight(150)
        self.method_tag_list.itemChanged.connect(self._on_editor_changed)
        self.method_search_edit = QLineEdit()
        self.method_search_edit.setPlaceholderText("搜索方法 / 思想标签")
        self.method_search_edit.textChanged.connect(self._filter_method_tags)
        method_layout.addWidget(self.method_search_edit)
        method_layout.addWidget(self.method_tag_list)
        editor_layout.addWidget(method_group)

        learning_group = QGroupBox("学习标签")
        learning_layout = QVBoxLayout(learning_group)
        learning_row = QHBoxLayout()
        add_learning_button = QPushButton("新增学习标签")
        add_learning_button.clicked.connect(self.add_learning_tag)
        learning_row.addWidget(add_learning_button)
        learning_row.addStretch(1)
        learning_layout.addLayout(learning_row)
        self.learning_tag_list = QListWidget()
        self.learning_tag_list.setMinimumHeight(120)
        self.learning_tag_list.itemChanged.connect(self._on_editor_changed)
        learning_layout.addWidget(self.learning_tag_list)
        editor_layout.addWidget(learning_group)
        self.tag_groups = [knowledge_group, method_group, learning_group]

        image_group = QGroupBox("题目配图（可选）")
        image_layout = QVBoxLayout(image_group)
        self.image_drop_label = ImageDropLabel()
        self.image_drop_label.image_file_dropped.connect(self._store_image_file)
        self.image_drop_label.image_dropped.connect(self._store_qimage)
        self.image_drop_label.paste_requested.connect(self.paste_image_from_clipboard)
        image_layout.addWidget(self.image_drop_label)
        self.question_images_list = QListWidget()
        self.question_images_list.setMaximumHeight(120)
        image_layout.addWidget(self.question_images_list)
        self.question_images_list.currentRowChanged.connect(lambda _: self._update_image_drop_preview())
        image_buttons = QHBoxLayout()
        paste_image_button = QPushButton("从剪贴板粘贴")
        paste_image_button.clicked.connect(self.paste_image_from_clipboard)
        choose_image_button = QPushButton("选择图片文件")
        choose_image_button.clicked.connect(self.choose_image_file)
        draw_image_button = QPushButton("软件内画图")
        draw_image_button.clicked.connect(self.draw_question_image)
        remove_image_button = QPushButton("移除配图")
        remove_image_button.clicked.connect(self.remove_question_image)
        image_buttons.addWidget(paste_image_button)
        image_buttons.addWidget(choose_image_button)
        image_buttons.addWidget(draw_image_button)
        image_buttons.addWidget(remove_image_button)
        image_layout.addLayout(image_buttons)
        editor_layout.addWidget(image_group)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor_container)
        self.edit_splitter.addWidget(editor_scroll)

        self.preview_panel = QWidget()
        self.preview_panel.setMinimumWidth(360)
        self.preview_panel.setMaximumWidth(560)
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_title = QLabel("实时预览（Command + 点击可定位原文）")
        preview_title.setObjectName("mutedText")
        preview_layout.addWidget(preview_title)
        self.edit_preview = QWebEngineView()
        self.edit_preview_page = PreviewSourcePage(self.edit_preview)
        self.edit_preview_page.source_location_requested.connect(self._focus_edit_source_location)
        self.edit_preview.setPage(self.edit_preview_page)
        self.edit_preview.loadFinished.connect(self._restore_edit_preview_scroll_position)
        preview_layout.addWidget(self.edit_preview, 1)
        self.edit_splitter.addWidget(self.preview_panel)
        self.edit_splitter.setChildrenCollapsible(False)
        self.edit_splitter.setSizes([1080, 440])
        self.edit_splitter.setStretchFactor(0, 4)
        self.edit_splitter.setStretchFactor(1, 1)
        return panel

    def _make_editor(self, placeholder: str) -> QPlainTextEdit:
        editor = ImagePasteEditor()
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(110)
        editor.setMinimumWidth(0)
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return editor

    def _toggle_edit_preview(self) -> None:
        visible = not self.preview_panel.isVisible()
        self.preview_panel.setVisible(visible)
        self.preview_toggle_button.setText("隐藏预览" if visible else "显示预览")

    def _toggle_tag_sections(self) -> None:
        self.tags_collapsed = not self.tags_collapsed
        for group in self.tag_groups:
            group.setVisible(not self.tags_collapsed)
        self.tag_section_toggle.setText("展开标签区" if self.tags_collapsed else "收起标签区")

    def _add_editor_section(self, layout: QVBoxLayout, title: str, editor: QPlainTextEdit) -> None:
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        header.addWidget(label)
        header.addStretch(1)
        expand_button = QPushButton("完整编辑")
        expand_button.setObjectName("secondaryButton")
        expand_button.setMinimumWidth(88)
        expand_button.clicked.connect(lambda _checked=False, field=editor, name=title: self.open_text_editor(name, field))
        header.addWidget(expand_button)
        layout.addLayout(header)
        layout.addWidget(editor)

    def open_text_editor(self, title: str, editor: QPlainTextEdit) -> None:
        dialog = LatexTextEditorDialog(title, editor.toPlainText(), self, self.editing_analysis_images if editor is self.analysis_edit else [])
        self._text_editor_dialogs.add(dialog)

        def finish(result: int) -> None:
            if result == QDialog.Accepted:
                editor.setPlainText(dialog.text())
                for image in dialog.attached_images:
                    self._store_qimage(image, analysis=editor is self.analysis_edit)
            self._text_editor_dialogs.discard(dialog)
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

    def draw_question_image(self) -> None:
        if not self.editing_question_id:
            return
        dialog = DrawingDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._store_qimage(dialog.image())

    def _populate_browse_tag_tree(self) -> None:
        with QSignalBlocker(self.browse_tag_tree):
            self.browse_tag_tree.clear()
            for book, chapters in KNOWLEDGE_TAG_TREE.items():
                book_item = self._make_browse_tag_item(book, book)
                self.browse_tag_tree.addTopLevelItem(book_item)
                for chapter, topics in chapters.items():
                    chapter_path = f"{book} / {chapter}"
                    chapter_item = self._make_browse_tag_item(chapter, chapter_path)
                    book_item.addChild(chapter_item)
                    for topic in topics:
                        topic_path = f"{chapter_path} / {topic}"
                        chapter_item.addChild(self._make_browse_tag_item(topic, topic_path))
                book_item.setExpanded(True)
        self.browse_tag_tree.expandToDepth(1)

    def _make_browse_tag_item(self, text: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Unchecked)
        return item

    def _populate_browse_method_filter_list(self) -> None:
        checked = self._selected_browse_method_filters() if hasattr(self, "browse_method_list") else set()
        with QSignalBlocker(self.browse_method_list):
            self.browse_method_list.clear()
            for tag in self.method_tag_library:
                item = QListWidgetItem(tag)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if tag in checked else Qt.Unchecked)
                self.browse_method_list.addItem(item)

    def _populate_browse_learning_filter_list(self) -> None:
        checked = self._selected_browse_learning_filters() if hasattr(self, "browse_learning_list") else set()
        with QSignalBlocker(self.browse_learning_list):
            self.browse_learning_list.clear()
            for tag in self.learning_tag_library:
                item = QListWidgetItem(tag)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if tag in checked else Qt.Unchecked)
                self.browse_learning_list.addItem(item)

    def _populate_learning_tag_list(self, selected_tags: list[str]) -> None:
        selected = set(selected_tags)
        with QSignalBlocker(self.learning_tag_list):
            self.learning_tag_list.clear()
            for tag in self.learning_tag_library:
                item = QListWidgetItem(tag)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if tag in selected else Qt.Unchecked)
                self.learning_tag_list.addItem(item)

    def _populate_edit_knowledge_tree(self) -> None:
        with QSignalBlocker(self.edit_knowledge_tree):
            self.edit_knowledge_tree.clear()
            for book, chapters in KNOWLEDGE_TAG_TREE.items():
                book_item = QTreeWidgetItem([book])
                book_item.setData(0, Qt.UserRole, book)
                book_item.setFlags(book_item.flags() | Qt.ItemIsUserCheckable)
                book_item.setCheckState(0, Qt.Unchecked)
                self.edit_knowledge_tree.addTopLevelItem(book_item)
                for chapter, topics in chapters.items():
                    chapter_item = QTreeWidgetItem([chapter])
                    chapter_item.setData(0, Qt.UserRole, f"{book} / {chapter}")
                    chapter_item.setFlags(chapter_item.flags() | Qt.ItemIsUserCheckable)
                    chapter_item.setCheckState(0, Qt.Unchecked)
                    book_item.addChild(chapter_item)
                    for topic in topics:
                        path = f"{book} / {chapter} / {topic}"
                        topic_item = QTreeWidgetItem([topic])
                        topic_item.setData(0, Qt.UserRole, path)
                        topic_item.setFlags(topic_item.flags() | Qt.ItemIsUserCheckable)
                        topic_item.setCheckState(0, Qt.Unchecked)
                        chapter_item.addChild(topic_item)
                book_item.setExpanded(True)
        self.edit_knowledge_tree.expandToDepth(1)

    def _populate_method_tag_list(self, selected_tags: list[str]) -> None:
        selected = set(selected_tags)
        with QSignalBlocker(self.method_tag_list):
            self.method_tag_list.clear()
            for tag in self.method_tag_library:
                item = QListWidgetItem(tag)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if tag in selected else Qt.Unchecked)
                self.method_tag_list.addItem(item)

    def _on_browse_knowledge_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._changing_browse_tags:
            return
        self._changing_browse_tags = True
        try:
            self._set_descendant_check_state(item, item.checkState(0))
        finally:
            self._changing_browse_tags = False
        self._refresh_browse_results()

    def _set_descendant_check_state(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_descendant_check_state(child, state)

    def _on_browse_filter_changed(self, *args) -> None:
        self._search_timer.stop()
        self._refresh_browse_results()

    def _schedule_search_refresh(self) -> None:
        self._search_timer.start()

    def _clear_browse_tag_filters(self, *, render: bool = True) -> None:
        with QSignalBlocker(self.browse_tag_tree), QSignalBlocker(self.browse_method_list), QSignalBlocker(self.browse_learning_list):
            self._set_all_tree_checks(self.browse_tag_tree.invisibleRootItem(), Qt.Unchecked)
            for row in range(self.browse_method_list.count()):
                self.browse_method_list.item(row).setCheckState(Qt.Unchecked)
            for row in range(self.browse_learning_list.count()):
                self.browse_learning_list.item(row).setCheckState(Qt.Unchecked)
        if render:
            self._refresh_browse_results()

    def _clear_all_browse_filters(self) -> None:
        with QSignalBlocker(self.search_edit), QSignalBlocker(self.question_type_filter_combo):
            self.search_edit.clear()
            self.question_type_filter_combo.setCurrentIndex(0)
        self._clear_browse_tag_filters(render=False)

    def _set_all_tree_checks(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_all_tree_checks(child, state)

    def _expand_all_browse_tags(self) -> None:
        self.browse_tag_tree.expandAll()

    def _selected_browse_tag_filters(self) -> list[str]:
        selected: list[str] = []
        root = self.browse_tag_tree.invisibleRootItem()

        def visit(item: QTreeWidgetItem) -> None:
            for index in range(item.childCount()):
                child = item.child(index)
                if child.checkState(0) == Qt.Checked:
                    selected.append(child.data(0, Qt.UserRole))
                visit(child)

        visit(root)
        return [value for value in selected if not any(value.startswith(f"{parent} / ") for parent in selected if parent != value)]

    def _selected_browse_method_filters(self) -> set[str]:
        return {
            self.browse_method_list.item(row).text()
            for row in range(self.browse_method_list.count())
            if self.browse_method_list.item(row).checkState() == Qt.Checked
        }

    def _selected_browse_learning_filters(self) -> set[str]:
        return {
            self.browse_learning_list.item(row).text()
            for row in range(self.browse_learning_list.count())
            if self.browse_learning_list.item(row).checkState() == Qt.Checked
        }

    def _set_browse_knowledge_filter(self, path: str) -> None:
        root = self.browse_tag_tree.invisibleRootItem()

        def visit(item: QTreeWidgetItem) -> bool:
            for index in range(item.childCount()):
                child = item.child(index)
                if child.data(0, Qt.UserRole) == path:
                    with QSignalBlocker(self.browse_tag_tree):
                        child.setCheckState(0, Qt.Checked)
                        self._set_descendant_check_state(child, Qt.Checked)
                    return True
                if visit(child):
                    return True
            return False

        visit(root)

    def _active_filter_summary(self) -> str:
        parts: list[str] = []
        keyword = self.search_edit.text().strip()
        if keyword:
            parts.append(f"关键词：{keyword}")
        if self.question_type_filter_combo.currentData():
            parts.append(f"题型：{self.question_type_filter_combo.currentText()}")
        knowledge = self._selected_browse_tag_filters()
        methods = self._selected_browse_method_filters()
        learning = self._selected_browse_learning_filters()
        if knowledge:
            parts.append(f"知识点 {len(knowledge)} 项")
        if methods:
            parts.append(f"方法/思想 {len(methods)} 项")
        if learning:
            parts.append(f"学习标签 {len(learning)} 项")
        return "当前筛选：" + " · ".join(parts) if parts else "当前筛选：全部题目"

    def _sync_basket_controls(self) -> None:
        if not hasattr(self, "active_basket_combo"):
            return
        current_id = self._current_basket().id
        with QSignalBlocker(self.active_basket_combo):
            self.active_basket_combo.clear()
            for basket in self.baskets:
                self.active_basket_combo.addItem(f"{basket.name}（{len(basket.question_ids)}）", basket.id)
            self.active_basket_combo.setCurrentIndex(max(0, self.active_basket_combo.findData(current_id)))

    def _switch_basket_from_toolbar(self) -> None:
        basket_id = self.active_basket_combo.currentData()
        if not basket_id or basket_id == self.active_basket_id:
            return
        self.active_basket_id = basket_id
        self.persist_state()
        self._refresh_browse_results(preserve_scroll=True)

    def _filtered_questions(self) -> list[Question]:
        keyword = self.search_edit.text().strip().lower()
        question_type = self.question_type_filter_combo.currentData()
        knowledge_filters = self._selected_browse_tag_filters()
        method_filters = self._selected_browse_method_filters()
        learning_filters = self._selected_browse_learning_filters()
        result = []
        for question in self.questions:
            if keyword and keyword not in self._question_index_text(question):
                continue
            if question_type and question.question_type != question_type:
                continue
            if knowledge_filters and not any(
                tag.startswith(prefix) for tag in question.knowledge_tags for prefix in knowledge_filters
            ):
                continue
            if method_filters and not method_filters.intersection(question.method_tags):
                continue
            if learning_filters and not learning_filters.intersection(question.learning_tags):
                continue
            result.append(question)
        return self._sort_questions_by_knowledge(result)

    def _question_index_text(self, question: Question) -> str:
        return " ".join(
            [
                question.stem,
                question.source,
                question.created_at,
                str(question.difficulty),
                question.answer,
                question.analysis,
                question.note,
                " ".join(question.knowledge_tags),
                " ".join(question.method_tags),
                " ".join(question.learning_tags),
            ]
        ).lower()

    def _refresh_browse_results(self, preserve_scroll: bool = False) -> None:
        if not hasattr(self, "browse_web"):
            return
        if preserve_scroll:
            self.browse_web.page().runJavaScript(
                "window.scrollY || 0",
                self._render_browse_results_at_current_scroll,
            )
            return
        self._render_browse_results(None)

    def _render_browse_results_at_current_scroll(self, scroll_y) -> None:
        try:
            position = int(scroll_y or 0)
        except (TypeError, ValueError):
            position = 0
        self._render_browse_results(position)

    def _render_browse_results(self, scroll_y: int | None) -> None:
        self._browse_render_generation += 1
        filtered = self._filtered_questions()
        basket = self._current_basket()
        self.browse_count_label.setText(f"正在准备 {len(filtered)} 道题…")
        self._sync_basket_controls()
        self.export_basket_button.setEnabled(bool(basket.question_ids))
        self.active_filter_label.setText(self._active_filter_summary())
        generation = self._browse_render_generation
        # Snapshot mutable data before the background renderer reads it.
        questions = [Question.from_dict(question.to_dict()) for question in filtered]
        basket_ids = set(basket.question_ids)
        expanded_ids = set(self.expanded_question_ids)
        basket_name = basket.name
        task = PreviewRenderTask(
            generation,
            True,
            lambda: render_browse_cards(questions, basket_ids, expanded_ids, basket_name),
        )
        task.signals.finished.connect(
            lambda version, full, rendered: self._apply_browse_render(version, rendered, scroll_y, len(questions))
        )
        self._browse_render_tasks[generation] = task
        self._browse_loading = True
        PREVIEW_RENDER_POOL.start(task)

    def _apply_browse_render(self, generation: int, rendered: str, scroll_y: int | None, count: int) -> None:
        self._browse_render_tasks.pop(generation, None)
        if generation != self._browse_render_generation:
            return
        self._browse_loading = False
        if not rendered:
            self.browse_count_label.setText("题目预览暂时无法加载，请重新筛选后重试。")
            return
        self.browse_count_label.setText(f"当前显示 {count} 道题，共 {len(self.questions)} 道题")
        self._pending_browse_scroll_y = scroll_y
        self.browse_web.setHtml(rendered, QUrl("https://preview.local/"))

    def _restore_browse_scroll_position(self, loaded: bool) -> None:
        if not loaded or self._pending_browse_scroll_y is None:
            return
        position = self._pending_browse_scroll_y
        self._pending_browse_scroll_y = None
        generation = self._browse_render_generation

        def restore() -> None:
            if generation == self._browse_render_generation:
                self.browse_web.page().runJavaScript(f"window.scrollTo(0, {position});")

        restore()
        # MathJax finishes after the document load event; restore once more after typesetting changes height.
        QTimer.singleShot(350, restore)

    def _handle_browse_card_action(self, action: str, question_id: str) -> None:
        question = self._get_question_by_id(question_id)
        if question is None:
            return
        if action == "detail":
            if question_id in self.expanded_question_ids:
                self.expanded_question_ids.remove(question_id)
            else:
                self.expanded_question_ids.add(question_id)
            self._refresh_browse_results(preserve_scroll=True)
        elif action == "basket":
            self._toggle_basket_question(question_id)
        elif action == "labels":
            self.edit_question_learning_tags(question_id)
        elif action == "edit":
            self.edit_question(question_id)
        elif action == "delete":
            self.delete_question(question_id)

    def _handle_overview_action(self, action: str, value: str) -> None:
        value = unquote(value)
        if action not in {"knowledge", "learning"} or not value:
            return
        self._clear_all_browse_filters()
        if action == "knowledge":
            self._set_browse_knowledge_filter(value)
        else:
            with QSignalBlocker(self.browse_learning_list):
                for row in range(self.browse_learning_list.count()):
                    item = self.browse_learning_list.item(row)
                    if item.text() == value:
                        item.setCheckState(Qt.Checked)
                        break
        self._set_mode("browse")
        self._refresh_browse_results()

    def _set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self._set_browse_sidebar_visible(mode != "edit")
        self.workspace_stack.setCurrentIndex({"browse": 0, "edit": 1, "overview": 2}.get(mode, 0))
        if mode == "browse":
            self._refresh_browse_results()
        elif mode == "overview":
            self._refresh_overview()

    def _set_browse_sidebar_visible(self, visible: bool) -> None:
        """Reserve the full workspace for writing; the sidebar is browse-only."""
        if visible == self.browse_sidebar.isVisible():
            return
        if not visible:
            self._browse_sidebar_sizes = self.root_splitter.sizes()
            self.browse_sidebar.hide()
            return
        self.browse_sidebar.show()
        sizes = self._browse_sidebar_sizes or [330, 1330]
        QTimer.singleShot(0, lambda: self.root_splitter.setSizes(sizes))

    def open_overview(self) -> None:
        was_editing = self.current_mode == "edit"
        if not self._confirm_leave_editor():
            return
        if was_editing:
            self._finish_edit_session()
        self._set_mode("overview")

    def open_browse(self) -> None:
        was_editing = self.current_mode == "edit"
        if not self._confirm_leave_editor():
            return
        if was_editing:
            self._finish_edit_session()
        self._set_mode("browse")

    def add_question(self) -> None:
        was_editing = self.current_mode == "edit"
        if not self._confirm_leave_editor():
            return
        if was_editing:
            self._finish_edit_session()
        self._loading_question = True
        try:
            self.editing_question_id = Question().id
            self._is_new_draft = True
            self._clear_editor_fields()
            self.edit_header_label.setText("新建题目")
        finally:
            self._loading_question = False
        self.update_edit_preview()
        self._set_mode("edit")
        self._set_editor_baseline()
        self.stem_edit.setFocus()

    def edit_question(self, question_id: str) -> None:
        was_editing = self.current_mode == "edit"
        if not self._confirm_leave_editor():
            return
        if was_editing:
            self._finish_edit_session()
        question = self._get_question_by_id(question_id)
        if question is None:
            return
        self._load_question_into_editor(question)
        self._set_mode("edit")

    def _load_question_into_editor(self, question: Question) -> None:
        self._loading_question = True
        try:
            self.editing_question_id = question.id
            self._is_new_draft = False
            index = self._find_question_index(question.id) or 0
            self.edit_header_label.setText(f"编辑题目：{question.title(index)}")
            self.question_type_combo.setCurrentText(question.question_type)
            self.difficulty_combo.setCurrentIndex(max(0, question.difficulty - 1))
            self.source_edit.setText(question.source)
            self.created_at_label.setText(question.created_at)
            self.editing_image_path = question.image_path
            self.editing_image_paths = list(question.question_images)
            self.editing_analysis_images = list(question.analysis_image_paths)
            self._refresh_image_lists()
            self._original_image_path = question.image_path
            self._update_image_drop_preview()
            self.stem_edit.setPlainText(question.stem)
            for editor, content in zip(self.option_edits, question.options):
                editor.setText(content)
            self.answer_edit.setPlainText(question.answer)
            self.analysis_edit.setPlainText(question.analysis)
            self.note_edit.setPlainText(question.note)
            self._set_checked_edit_knowledge_tags(question.knowledge_tags)
            self._populate_method_tag_list(question.method_tags)
            self._populate_learning_tag_list(question.learning_tags)
            self._toggle_options_visibility(question.question_type)
        finally:
            self._loading_question = False
        self.update_edit_preview()
        self._set_editor_baseline()

    def _clear_editor_fields(self) -> None:
        self.question_type_combo.setCurrentText(QuestionType.SINGLE.value)
        self.difficulty_combo.setCurrentIndex(2)
        self.source_edit.clear()
        self.created_at_label.setText(Question().created_at)
        self.editing_image_path = ""
        self.editing_image_paths = []
        self.editing_analysis_images = []
        self._refresh_image_lists()
        self._original_image_path = ""
        self._update_image_drop_preview()
        self.stem_edit.clear()
        for editor in self.option_edits:
            editor.clear()
        self.answer_edit.clear()
        self.analysis_edit.clear()
        self.note_edit.clear()
        self._set_checked_edit_knowledge_tags([])
        self._populate_method_tag_list([])
        self._populate_learning_tag_list([])
        self._toggle_options_visibility(QuestionType.SINGLE.value)

    def cancel_edit(self) -> None:
        if self._confirm_leave_editor():
            self._finish_edit_session()
            self._set_mode("browse")

    def save_and_return_to_browse(self) -> None:
        if self.save_current_question():
            self._finish_edit_session()
            self._set_mode("browse")

    def _on_editor_changed(self, *args) -> None:
        if self._loading_question:
            return
        self._toggle_options_visibility(self.question_type_combo.currentText())
        self.update_edit_preview()

    def _editor_snapshot(self) -> dict:
        return {
            "question_type": self.question_type_combo.currentText(),
            "difficulty": self.difficulty_combo.currentData(),
            "source": self.source_edit.text(),
            "image_path": self.editing_image_path,
            "image_paths": list(self.editing_image_paths),
            "analysis_images": list(self.editing_analysis_images),
            "stem": self.stem_edit.toPlainText(),
            "options": [editor.text() for editor in self.option_edits],
            "answer": self.answer_edit.toPlainText(),
            "analysis": self.analysis_edit.toPlainText(),
            "note": self.note_edit.toPlainText(),
            "knowledge": self._selected_edit_knowledge_tags(),
            "method": self._selected_method_tags(),
            "learning": self._selected_learning_tags(),
        }

    def _set_editor_baseline(self) -> None:
        self._editor_baseline = self._editor_snapshot()

    def _editor_has_unsaved_changes(self) -> bool:
        return bool(self.editing_question_id and self._editor_baseline is not None and self._editor_snapshot() != self._editor_baseline)

    def _confirm_leave_editor(self) -> bool:
        if self.current_mode != "edit" or not self.editing_question_id or not self._editor_has_unsaved_changes():
            return True
        prompt = QMessageBox(self)
        prompt.setWindowTitle("未保存的更改")
        prompt.setText("这道题有尚未保存的修改。")
        prompt.setInformativeText("请选择保存后离开、放弃本次修改，或继续编辑。")
        save_button = prompt.addButton("保存并离开", QMessageBox.AcceptRole)
        discard_button = prompt.addButton("放弃修改", QMessageBox.DestructiveRole)
        prompt.addButton("继续编辑", QMessageBox.RejectRole)
        prompt.exec()
        if prompt.clickedButton() == save_button:
            return self.save_current_question()
        if prompt.clickedButton() == discard_button:
            self._discard_draft_image()
            return True
        return False

    def _finish_edit_session(self) -> None:
        self.editing_question_id = None
        self._is_new_draft = False
        self._editor_baseline = None
        self._original_image_path = ""

    def _toggle_options_visibility(self, question_type: str) -> None:
        self.options_group.setVisible(question_type in CHOICE_TYPES)

    def update_edit_preview(self) -> None:
        self._edit_preview_timer.start()

    def _request_edit_preview_render(self) -> None:
        if not self._edit_preview_loaded:
            self._set_edit_preview_html(0)
            return
        self.edit_preview.page().runJavaScript(
            "window.scrollY || 0",
            self._set_edit_preview_html,
        )

    def _set_edit_preview_html(self, scroll_y) -> None:
        try:
            self._pending_edit_preview_scroll_y = max(0, int(scroll_y or 0))
        except (TypeError, ValueError):
            self._pending_edit_preview_scroll_y = 0
        self._edit_preview_render_generation += 1
        question = self._build_question_from_editor()
        generation = self._edit_preview_render_generation
        full_document = not self._edit_preview_loaded
        renderer = (
            lambda: render_question_preview(question)
            if full_document
            else render_question_preview_fragment(question)
        )
        task = PreviewRenderTask(generation, full_document, renderer)
        task.signals.finished.connect(self._apply_edit_preview_render)
        self._edit_preview_tasks[generation] = task
        PREVIEW_RENDER_POOL.start(task)

    def _apply_edit_preview_render(self, generation: int, full_document: bool, rendered: str) -> None:
        self._edit_preview_tasks.pop(generation, None)
        if generation != self._edit_preview_render_generation or not rendered:
            return
        if full_document:
            self.edit_preview.setHtml(rendered, QUrl("https://preview.local/"))
            return
        self.edit_preview.page().runJavaScript(
            _replace_preview_content_script(rendered, self._pending_edit_preview_scroll_y or 0),
        )

    def _restore_edit_preview_scroll_position(self, loaded: bool) -> None:
        if not loaded:
            return
        self._edit_preview_loaded = True
        if self._pending_edit_preview_scroll_y is None:
            return
        position = self._pending_edit_preview_scroll_y
        generation = self._edit_preview_render_generation

        def restore() -> None:
            if generation == self._edit_preview_render_generation:
                self.edit_preview.page().runJavaScript(f"window.scrollTo(0, {position});")

        restore()
        QTimer.singleShot(260, restore)

    def _focus_edit_source_location(self, target: str, offset: int) -> None:
        editor = {
            "stem": self.stem_edit,
            "answer": self.answer_edit,
            "analysis": self.analysis_edit,
            "note": self.note_edit,
        }.get(target)
        if editor is not None:
            cursor = editor.textCursor()
            cursor.setPosition(min(max(0, offset), len(editor.toPlainText())))
            editor.setTextCursor(cursor)
            editor.setFocus()
            editor.ensureCursorVisible()
            return
        if not target.startswith("option_"):
            return
        try:
            index = int(target.removeprefix("option_"))
        except ValueError:
            return
        if not 0 <= index < len(self.option_edits):
            return
        option = self.option_edits[index]
        option.setCursorPosition(min(max(0, offset), len(option.text())))
        option.setFocus()

    def _build_question_from_editor(self) -> Question:
        original = self._get_question_by_id(self.editing_question_id)
        question = Question.from_dict(original.to_dict()) if original else Question(id=self.editing_question_id or Question().id)
        question.question_type = self.question_type_combo.currentText()
        question.difficulty = int(self.difficulty_combo.currentData())
        question.source = self.source_edit.text().strip()
        question.created_at = self.created_at_label.text() or question.created_at
        question.image_path = self.editing_image_path
        question.image_paths = list(self.editing_image_paths)
        question.image_path = question.image_paths[0] if question.image_paths else ""
        question.analysis_image_paths = list(self.editing_analysis_images)
        question.stem = normalize_mixed_latex_text(self.stem_edit.toPlainText())
        question.options = [normalize_mixed_latex_text(editor.text()) for editor in self.option_edits]
        question.answer = normalize_mixed_latex_text(self.answer_edit.toPlainText())
        question.analysis = normalize_mixed_latex_text(self.analysis_edit.toPlainText())
        question.note = normalize_mixed_latex_text(self.note_edit.toPlainText())
        question.knowledge_tags = self._selected_edit_knowledge_tags()
        question.method_tags = self._selected_method_tags()
        question.learning_tags = self._selected_learning_tags()
        return question

    def paste_image_from_clipboard(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "剪贴板中没有图片", "请先复制一张图片，或使用“选择图片文件”。")
            return
        self._store_qimage(image)

    def choose_image_file(self) -> None:
        sources, _ = QFileDialog.getOpenFileNames(
            self,
            "选择题目配图",
            str(Path.home()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        for source in sources:
            self._store_image_file(source)

    def _store_image_file(self, source: str) -> None:
        image = QImage(source)
        if image.isNull():
            QMessageBox.warning(self, "无法读取图片", "请选择有效的图片文件。")
            return
        self._store_qimage(image)

    def _store_qimage(self, image: QImage, *, analysis: bool = False) -> None:
        if image.isNull() or not self.editing_question_id:
            return
        target = self.draft_image_directory / f"{self.editing_question_id}-{uuid4().hex}.png"
        if not image.save(str(target), "PNG"):
            QMessageBox.warning(self, "保存图片失败", "无法将图片保存到题库资源目录。")
            return
        paths = self.editing_analysis_images if analysis else self.editing_image_paths
        paths.append(str(target.resolve()))
        self.editing_image_path = self.editing_image_paths[0] if self.editing_image_paths else ""
        self._refresh_image_lists()
        self._update_image_drop_preview()
        self._on_editor_changed()

    def remove_question_image(self) -> None:
        self._remove_gallery_image(False)

    def _refresh_image_lists(self) -> None:
        for widget, paths in [(self.question_images_list, self.editing_image_paths), (self.analysis_images_list, self.editing_analysis_images)]:
            with QSignalBlocker(widget):
                widget.clear()
                for index, path in enumerate(paths, 1):
                    item = QListWidgetItem(f"图片 {index}")
                    item.setToolTip(path)
                    item.setIcon(QIcon(path))
                    widget.addItem(item)
                if paths:
                    widget.setCurrentRow(len(paths) - 1)

    def _remove_gallery_image(self, analysis: bool) -> None:
        widget = self.analysis_images_list if analysis else self.question_images_list
        paths = self.editing_analysis_images if analysis else self.editing_image_paths
        row = widget.currentRow()
        if not 0 <= row < len(paths):
            return
        removed = Path(paths.pop(row))
        if removed.parent == self.draft_image_directory.resolve():
            removed.unlink(missing_ok=True)
        self.editing_image_path = self.editing_image_paths[0] if self.editing_image_paths else ""
        self._refresh_image_lists()
        self._update_image_drop_preview()
        self._on_editor_changed()

    def _update_image_drop_preview(self) -> None:
        row = self.question_images_list.currentRow()
        path = self.editing_image_paths[row] if 0 <= row < len(self.editing_image_paths) else self.editing_image_path
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image_drop_label.setPixmap(QPixmap())
            self.image_drop_label.setText("将图片文件拖到这里，或单击后使用粘贴快捷键")
            return
        self.image_drop_label.setText("")
        self.image_drop_label.setPixmap(
            pixmap.scaled(420, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def save_current_question(self) -> bool:
        if self._loading_question or not self.editing_question_id:
            return False
        if not self.stem_edit.toPlainText().strip():
            QMessageBox.warning(self, "题目不能为空", "请先填写题干后再保存。")
            self.stem_edit.setFocus()
            return False
        question = self._build_question_from_editor()
        original = self._get_question_by_id(self.editing_question_id)
        original_images = list(original.all_images) if original else []
        self._finalize_draft_image(question)
        index = self._find_question_index(self.editing_question_id)
        if index is None:
            self.questions.append(question)
        else:
            self.questions[index] = question
        self.state.questions = self.questions
        self._cleanup_unused_image(self._original_image_path)
        for path in original_images:
            self._cleanup_unused_image(path)
        self._is_new_draft = False
        self.persist_state()
        self._original_image_path = question.image_path
        self._set_editor_baseline()
        return True

    def _draft_image_path(self) -> Path:
        return self.draft_image_directory / f"{self.editing_question_id}.png"

    def _discard_draft_image(self) -> None:
        if not self.editing_question_id:
            return
        for path in self.draft_image_directory.glob(f"{self.editing_question_id}*.png"):
            path.unlink(missing_ok=True)

    def _finalize_draft_image(self, question: Question) -> None:
        for paths in [question.image_paths, question.analysis_image_paths]:
            for index, path in enumerate(paths):
                draft = Path(path)
                if draft.parent == self.draft_image_directory.resolve() and draft.is_file():
                    target = self.image_directory / draft.name
                    shutil.copy2(draft, target)
                    paths[index] = str(target.resolve())
                    draft.unlink(missing_ok=True)
        question.image_path = question.image_paths[0] if question.image_paths else ""
        self.editing_image_paths = list(question.image_paths)
        self.editing_analysis_images = list(question.analysis_image_paths)
        self.editing_image_path = question.image_path
        self._refresh_image_lists()

    def _cleanup_unused_image(self, image_path: str) -> None:
        if not image_path:
            return
        try:
            image = Path(image_path).resolve()
        except OSError:
            return
        if image.parent != self.image_directory.resolve():
            return
        if any(str(image) in question.all_images for question in self.questions):
            return
        image.unlink(missing_ok=True)

    def _selected_edit_knowledge_tags(self) -> list[str]:
        selected: list[str] = []
        root = self.edit_knowledge_tree.invisibleRootItem()
        for book_index in range(root.childCount()):
            book_item = root.child(book_index)
            if book_item.checkState(0) == Qt.Checked:
                selected.append(book_item.data(0, Qt.UserRole))
            for chapter_index in range(book_item.childCount()):
                chapter_item = book_item.child(chapter_index)
                if chapter_item.checkState(0) == Qt.Checked:
                    selected.append(chapter_item.data(0, Qt.UserRole))
                for topic_index in range(chapter_item.childCount()):
                    topic_item = chapter_item.child(topic_index)
                    if topic_item.checkState(0) == Qt.Checked:
                        selected.append(topic_item.data(0, Qt.UserRole))
        return selected

    def _set_checked_edit_knowledge_tags(self, selected_tags: list[str]) -> None:
        selected = set(selected_tags)
        with QSignalBlocker(self.edit_knowledge_tree):
            root = self.edit_knowledge_tree.invisibleRootItem()
            for book_index in range(root.childCount()):
                book_item = root.child(book_index)
                book_item.setCheckState(0, Qt.Checked if book_item.data(0, Qt.UserRole) in selected else Qt.Unchecked)
                for chapter_index in range(book_item.childCount()):
                    chapter_item = book_item.child(chapter_index)
                    chapter_item.setCheckState(0, Qt.Checked if chapter_item.data(0, Qt.UserRole) in selected else Qt.Unchecked)
                    for topic_index in range(chapter_item.childCount()):
                        topic_item = chapter_item.child(topic_index)
                        topic_item.setCheckState(0, Qt.Checked if topic_item.data(0, Qt.UserRole) in selected else Qt.Unchecked)

    def _selected_method_tags(self) -> list[str]:
        return [
            self.method_tag_list.item(row).text()
            for row in range(self.method_tag_list.count())
            if self.method_tag_list.item(row).checkState() == Qt.Checked
        ]

    def _selected_learning_tags(self) -> list[str]:
        return [
            self.learning_tag_list.item(row).text()
            for row in range(self.learning_tag_list.count())
            if self.learning_tag_list.item(row).checkState() == Qt.Checked
        ]

    def add_method_tag(self) -> None:
        text, ok = QInputDialog.getText(self, "新增方法标签", "请输入方法/思想标签：")
        tag = text.strip()
        if not ok or not tag:
            return
        selected = self._selected_method_tags()
        if tag not in self.method_tag_library:
            self.method_tag_library = self._merge_method_tags([*self.method_tag_library, tag])
            self._populate_browse_method_filter_list()
        if tag not in selected:
            selected.append(tag)
        self._populate_method_tag_list(selected)
        self._filter_method_tags(self.method_search_edit.text())
        self.persist_state()
        self._on_editor_changed()

    def _filter_method_tags(self, text: str) -> None:
        for widget in [self.method_tag_list, self.browse_method_list]:
            for row in range(widget.count()):
                item = widget.item(row)
                item.setHidden(text.casefold() not in item.text().casefold())

    def rename_method_tag(self) -> None:
        self._change_method_tag(False)

    def delete_method_tag(self) -> None:
        self._change_method_tag(True)

    def _change_method_tag(self, delete: bool) -> None:
        widget = self.method_tag_list if self.current_mode == "edit" else self.browse_method_list
        item = widget.currentItem()
        if item is None:
            QMessageBox.information(self, "选择标签", "请先单击选择要管理的方法 / 思想标签。")
            return
        old = item.text()
        new = ""
        if delete:
            if QMessageBox.question(self, "删除方法标签", f"删除“{old}”并从所有题目中移除吗？") != QMessageBox.Yes:
                return
        else:
            new, ok = QInputDialog.getText(self, "编辑方法标签", "标签名称：", text=old)
            new = new.strip()
            if not ok or not new or new == old:
                return
            if new in self.method_tag_library:
                QMessageBox.warning(self, "名称重复", "这个标签名称已存在。")
                return
        selected = [new if tag == old else tag for tag in self._selected_method_tags() if tag != old or new]
        self.method_tag_library = [new if tag == old else tag for tag in self.method_tag_library if tag != old or new]
        for question in self.questions:
            question.method_tags = [new if tag == old else tag for tag in question.method_tags if tag != old or new]
        self._populate_method_tag_list(selected)
        self._populate_browse_method_filter_list()
        self.persist_state()
        self._on_editor_changed()
        self._refresh_browse_results(preserve_scroll=True)

    def add_learning_tag(self) -> None:
        text, ok = QInputDialog.getText(self, "新增学习标签", "请输入学习标签（例如：错题、好题、易错）：")
        tag = text.strip()
        if not ok or not tag:
            return
        selected = self._selected_learning_tags() if hasattr(self, "learning_tag_list") else []
        if tag not in self.learning_tag_library:
            self.learning_tag_library = self._unique_tags([*self.learning_tag_library, tag])
        if tag not in selected:
            selected.append(tag)
        self._populate_browse_learning_filter_list()
        self._populate_learning_tag_list(selected)
        self.persist_state()
        self._on_editor_changed()

    def rename_learning_tag(self) -> None:
        item = self.browse_learning_list.currentItem()
        if item is None:
            QMessageBox.information(self, "选择学习标签", "请先在左侧学习标签列表中选中要重命名的标签。")
            return
        old_name = item.text()
        text, ok = QInputDialog.getText(self, "重命名学习标签", "新名称：", text=old_name)
        new_name = text.strip()
        if not ok or not new_name or new_name == old_name:
            return
        if new_name in self.learning_tag_library:
            QMessageBox.warning(self, "名称已存在", "已有同名学习标签，请使用其他名称。")
            return
        self.learning_tag_library = [new_name if tag == old_name else tag for tag in self.learning_tag_library]
        for question in self.questions:
            question.learning_tags = [new_name if tag == old_name else tag for tag in question.learning_tags]
        self._populate_browse_learning_filter_list()
        self._populate_learning_tag_list(self._selected_learning_tags())
        self.persist_state()
        self._refresh_browse_results(preserve_scroll=True)

    def delete_learning_tag(self) -> None:
        item = self.browse_learning_list.currentItem()
        if item is None:
            QMessageBox.information(self, "选择学习标签", "请先在左侧学习标签列表中选中要删除的标签。")
            return
        tag = item.text()
        if QMessageBox.question(self, "确认删除", f"删除学习标签“{tag}”吗？题目不会被删除。") != QMessageBox.Yes:
            return
        self.learning_tag_library = [value for value in self.learning_tag_library if value != tag]
        for question in self.questions:
            question.learning_tags = [value for value in question.learning_tags if value != tag]
        self._populate_browse_learning_filter_list()
        self._populate_learning_tag_list(self._selected_learning_tags())
        self.persist_state()
        self._refresh_browse_results(preserve_scroll=True)

    def edit_question_learning_tags(self, question_id: str) -> None:
        question = self._get_question_by_id(question_id)
        if question is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑学习标签")
        dialog.resize(380, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("为这道题勾选学习标签："))
        tag_list = QListWidget()
        for tag in self.learning_tag_library:
            item = QListWidgetItem(tag)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if tag in question.learning_tags else Qt.Unchecked)
            tag_list.addItem(item)
        layout.addWidget(tag_list, 1)
        buttons = QHBoxLayout()
        add_button = QPushButton("新增标签")

        def add_tag() -> None:
            text, ok = QInputDialog.getText(dialog, "新增学习标签", "标签名称：")
            tag = text.strip()
            if not ok or not tag:
                return
            if tag not in self.learning_tag_library:
                self.learning_tag_library.append(tag)
            if not any(tag_list.item(row).text() == tag for row in range(tag_list.count())):
                item = QListWidgetItem(tag)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                tag_list.addItem(item)

        add_button.clicked.connect(add_tag)
        save_button = QPushButton("保存")
        save_button.setObjectName("primaryButton")

        def save_tags() -> None:
            question.learning_tags = [
                tag_list.item(row).text()
                for row in range(tag_list.count())
                if tag_list.item(row).checkState() == Qt.Checked
            ]
            self.learning_tag_library = self._unique_tags(self.learning_tag_library)
            self._populate_browse_learning_filter_list()
            self._populate_learning_tag_list(question.learning_tags if self.editing_question_id == question.id else self._selected_learning_tags())
            self.persist_state()
            dialog.accept()
            self._refresh_browse_results(preserve_scroll=True)

        save_button.clicked.connect(save_tags)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(dialog.reject)
        buttons.addWidget(add_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        dialog.exec()

    def _toggle_basket_question(self, question_id: str) -> None:
        basket = self._current_basket()
        if question_id in basket.question_ids:
            basket.question_ids.remove(question_id)
        else:
            basket.question_ids.append(question_id)
        self.persist_state()
        self._refresh_browse_results(preserve_scroll=True)

    def open_basket(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("管理试题篮")
        dialog.resize(660, 460)
        layout = QVBoxLayout(dialog)
        selector = RefinedComboBox()
        selector.setMinimumWidth(240)
        layout.addWidget(selector)
        summary = QLabel()
        layout.addWidget(summary)
        question_list = QListWidget()
        question_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(question_list, 1)
        basket_actions = QHBoxLayout()
        new_basket_button = QPushButton("新建试题篮")
        rename_basket_button = QPushButton("重命名")
        delete_basket_button = QPushButton("删除当前篮子")
        basket_actions.addWidget(new_basket_button)
        basket_actions.addWidget(rename_basket_button)
        basket_actions.addWidget(delete_basket_button)
        basket_actions.addStretch(1)
        layout.addLayout(basket_actions)
        buttons = QHBoxLayout()
        remove_button = QPushButton("移除选中题目")

        def reload_baskets() -> None:
            with QSignalBlocker(selector):
                selector.clear()
                for basket in self.baskets:
                    selector.addItem(basket.name, basket.id)
                selector.setCurrentIndex(max(0, selector.findData(self.active_basket_id)))
            reload_questions()
            self._sync_basket_controls()

        def reload_questions() -> None:
            basket = self._current_basket()
            summary.setText(f"“{basket.name}”中共有 {len(basket.question_ids)} 道题。选中题目后可移出。")
            question_list.clear()
            for index, question in enumerate(self._basket_questions(), start=1):
                item = QListWidgetItem(question.title(index))
                item.setData(Qt.UserRole, question.id)
                question_list.addItem(item)

        def switch_basket() -> None:
            selected_id = selector.currentData()
            if selected_id:
                self.active_basket_id = selected_id
                self.persist_state()
                reload_questions()
                self._sync_basket_controls()
                self._refresh_browse_results()

        def new_basket() -> None:
            text, ok = QInputDialog.getText(dialog, "新建试题篮", "试题篮名称：")
            name = text.strip()
            if not ok or not name:
                return
            if any(basket.name == name for basket in self.baskets):
                QMessageBox.warning(dialog, "名称已存在", "请使用其他名称。")
                return
            basket = QuestionBasket(name=name)
            self.baskets.append(basket)
            self.active_basket_id = basket.id
            self.persist_state()
            reload_baskets()
            self._refresh_browse_results()

        def rename_basket() -> None:
            basket = self._current_basket()
            text, ok = QInputDialog.getText(dialog, "重命名试题篮", "新名称：", text=basket.name)
            name = text.strip()
            if not ok or not name or name == basket.name:
                return
            if any(other.name == name and other.id != basket.id for other in self.baskets):
                QMessageBox.warning(dialog, "名称已存在", "请使用其他名称。")
                return
            basket.name = name
            self.persist_state()
            reload_baskets()
            self._refresh_browse_results()

        def delete_basket() -> None:
            basket = self._current_basket()
            if QMessageBox.question(dialog, "确认删除", f"删除试题篮“{basket.name}”吗？题目不会被删除。") != QMessageBox.Yes:
                return
            self.baskets = [other for other in self.baskets if other.id != basket.id]
            if not self.baskets:
                self.baskets = [QuestionBasket()]
            self.active_basket_id = self.baskets[0].id
            self.persist_state()
            reload_baskets()
            self._refresh_browse_results()

        def remove_selected() -> None:
            selected = {item.data(Qt.UserRole) for item in question_list.selectedItems()}
            if not selected:
                return
            basket = self._current_basket()
            basket.question_ids = [question_id for question_id in basket.question_ids if question_id not in selected]
            self.persist_state()
            reload_questions()
            self._refresh_browse_results()

        remove_button.clicked.connect(remove_selected)
        selector.currentIndexChanged.connect(switch_basket)
        new_basket_button.clicked.connect(new_basket)
        rename_basket_button.clicked.connect(rename_basket)
        delete_basket_button.clicked.connect(delete_basket)
        export_button = QPushButton("导出题目篮")
        export_button.clicked.connect(lambda: (dialog.accept(), self.export_basket_questions()))
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        buttons.addWidget(export_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        reload_baskets()
        dialog.exec()

    def export_basket_questions(self) -> None:
        questions = self._basket_questions()
        if not questions:
            QMessageBox.information(self, "试题篮为空", f"“{self._current_basket().name}”中还没有题目。")
            return
        dialog = ExportOptionsDialog(self.math_type_status, self)
        if dialog.exec() == 0:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出 LaTeX 与 PDF",
            str(self.data_file.parent / f"{self._current_basket().name}.tex"),
            "LaTeX 源文件 (*.tex)",
        )
        if not target:
            return
        try:
            result = export_questions_to_latex_bundle(questions, target, dialog.build_options())
        except LatexExportError as error:
            QMessageBox.critical(self, "导出失败", f"LaTeX / PDF 导出失败：\n{error}")
            return
        message = f"已导出“{self._current_basket().name}”中的 {len(questions)} 道题。\n\n.tex 文件：\n{result.tex_path}"
        if result.pdf_path:
            message += f"\n\n.pdf 文件：\n{result.pdf_path}"
        QMessageBox.information(self, "导出完成", message)

    def delete_question(self, question_id: str) -> None:
        question = self._get_question_by_id(question_id)
        if question is None:
            return
        answer = QMessageBox.question(self, "确认删除", "确定删除这道题吗？")
        if answer != QMessageBox.Yes:
            return
        self.questions = [item for item in self.questions if item.id != question_id]
        self.state.questions = self.questions
        for path in question.all_images:
            self._cleanup_unused_image(path)
        for basket in self.baskets:
            basket.question_ids = [item for item in basket.question_ids if item != question_id]
        self.expanded_question_ids.discard(question_id)
        self.persist_state()
        self._refresh_browse_results()

    def _basket_questions(self) -> list[Question]:
        by_id = {question.id: question for question in self.questions}
        return [by_id[question_id] for question_id in self._current_basket().question_ids if question_id in by_id]

    def _valid_baskets(self, baskets: list[QuestionBasket]) -> list[QuestionBasket]:
        valid = {question.id for question in self.questions}
        result: list[QuestionBasket] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for basket in baskets:
            if not basket.id or basket.id in seen_ids:
                basket.id = QuestionBasket().id
            basket.name = basket.name.strip() or "未命名试题篮"
            if basket.name in seen_names:
                basket.name = f"{basket.name}（{len(result) + 1}）"
            basket.question_ids = [question_id for question_id in basket.question_ids if question_id in valid]
            basket.question_ids = list(dict.fromkeys(basket.question_ids))
            seen_ids.add(basket.id)
            seen_names.add(basket.name)
            result.append(basket)
        return result or [QuestionBasket()]

    def _current_basket(self) -> QuestionBasket:
        basket = next((item for item in self.baskets if item.id == self.active_basket_id), None)
        if basket is None:
            basket = self.baskets[0]
            self.active_basket_id = basket.id
        return basket

    def _unique_tags(self, tags: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip() for tag in tags if tag and tag.strip()))

    def _sort_questions_by_knowledge(self, questions: list[Question]) -> list[Question]:
        order = {tag: index for index, tag in enumerate(flatten_knowledge_tags())}
        for tag, rank in list(order.items()):
            parts = tag.split(" / ")
            for length in range(1, len(parts)):
                order.setdefault(" / ".join(parts[:length]), rank)
        untagged_rank = len(order) + 1

        def sort_key(question: Question) -> tuple[int, int, str]:
            ranks = [order[tag] for tag in question.knowledge_tags if tag in order]
            rank = min(ranks) if ranks else untagged_rank
            try:
                created_rank = -date.fromisoformat(question.created_at).toordinal()
            except ValueError:
                created_rank = 0
            return rank, created_rank, question.id

        return sorted(questions, key=sort_key)

    def _chapter_counts(self) -> list[tuple[str, int]]:
        counts: list[tuple[str, int]] = []
        for book, chapters in KNOWLEDGE_TAG_TREE.items():
            for chapter in chapters:
                prefix = f"{book} / {chapter}"
                count = sum(any(tag.startswith(prefix) for tag in question.knowledge_tags) for question in self.questions)
                counts.append((prefix, count))
        return counts

    def _refresh_overview(self) -> None:
        if hasattr(self, "overview_web"):
            self.overview_web.setHtml(render_overview(self.questions, self._chapter_counts()), QUrl("https://preview.local/"))

    def backup_library(self) -> None:
        default_name = f"题库备份-{date.today().isoformat()}.zip"
        target, _ = QFileDialog.getSaveFileName(
            self,
            "备份题库",
            str(self.data_file.parent / default_name),
            "题库备份 (*.zip)",
        )
        if not target:
            return
        if not target.lower().endswith(".zip"):
            target += ".zip"
        try:
            backup_path = create_backup(self.repository, self.state, target)
        except BackupError as error:
            QMessageBox.critical(self, "备份失败", str(error))
            return
        QMessageBox.information(self, "备份完成", f"题目、标签、试题篮和配图已备份到：\n{backup_path}")

    def restore_library(self) -> None:
        was_editing = self.current_mode == "edit"
        if not self._confirm_leave_editor():
            return
        if was_editing:
            self._finish_edit_session()
            self._set_mode("browse")
        source, _ = QFileDialog.getOpenFileName(self, "恢复题库", str(self.data_file.parent), "题库备份 (*.zip)")
        if not source:
            return
        try:
            restored = read_backup(self.repository, source)
        except BackupError as error:
            QMessageBox.critical(self, "无法恢复", str(error))
            return

        prompt = QMessageBox(self)
        prompt.setWindowTitle("恢复题库")
        prompt.setText("请选择恢复方式。")
        prompt.setInformativeText(
            f"备份包含 {len(restored.state.questions)} 道题、{len(restored.state.learning_tag_library)} 个学习标签、"
            f"{len(restored.state.baskets)} 个试题篮和 {len(restored.image_sources)} 张配图。\n\n"
            "合并会保留当前内容；覆盖会替换当前题目、标签和试题篮。"
        )
        merge_button = prompt.addButton("合并到当前题库", QMessageBox.AcceptRole)
        replace_button = prompt.addButton("覆盖当前题库", QMessageBox.DestructiveRole)
        prompt.addButton(QMessageBox.Cancel)
        prompt.exec()
        clicked = prompt.clickedButton()
        if clicked not in (merge_button, replace_button):
            discard_restored_backup(restored)
            return
        if clicked == replace_button:
            final_confirm = QMessageBox.warning(
                self,
                "最终确认覆盖",
                "覆盖恢复会替换当前题目、学习标签和试题篮。此操作无法撤销。\n\n确定继续吗？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if final_confirm != QMessageBox.Yes:
                discard_restored_backup(restored)
                return
        try:
            state = apply_restored_backup(
                restored,
                self.state,
                self.image_directory,
                merge=clicked == merge_button,
            )
            self._adopt_state(state)
            self.persist_state()
        except (BackupError, OSError) as error:
            QMessageBox.critical(self, "恢复失败", str(error))
            return
        self._refresh_browse_results()
        self._refresh_overview()
        mode = "已合并到当前题库" if clicked == merge_button else "已覆盖当前题库"
        QMessageBox.information(
            self,
            "恢复完成",
            f"{mode}。\n当前共有 {len(self.questions)} 道题、{len(self.learning_tag_library)} 个学习标签、"
            f"{len(self.baskets)} 个试题篮；已处理 {len(restored.image_sources)} 张配图。",
        )

    def _adopt_state(self, state) -> None:
        self.state = state
        self.questions = state.questions
        self.method_tag_library = self._merge_method_tags(state.method_tag_library)
        self.learning_tag_library = self._unique_tags(state.learning_tag_library)
        self.baskets = self._valid_baskets(state.baskets)
        self.active_basket_id = state.active_basket_id if state.active_basket_id else self.baskets[0].id
        if self.active_basket_id not in {basket.id for basket in self.baskets}:
            self.active_basket_id = self.baskets[0].id
        self._populate_browse_method_filter_list()
        self._populate_browse_learning_filter_list()
        self._populate_method_tag_list([])
        self._populate_learning_tag_list([])

    def _restore_splitter_state(self) -> None:
        root_state = self.settings.value("rootSplitter")
        edit_state = self.settings.value("editSplitter")
        if root_state:
            self.root_splitter.restoreState(root_state)
        if edit_state:
            self.edit_splitter.restoreState(edit_state)
        # Older versions stored a near-even split. Preserve deliberate resizing,
        # but repair historical layouts that leave the editor impractically narrow.
        QTimer.singleShot(0, self._ensure_editing_width)

    def _ensure_editing_width(self) -> None:
        if not self.preview_panel.isVisible():
            return
        sizes = self.edit_splitter.sizes()
        if len(sizes) != 2:
            return
        total_width = sum(sizes)
        if total_width <= 0:
            return
        desired_preview = min(500, max(360, int(total_width * 0.29)))
        desired_editor = total_width - desired_preview
        minimum_editor = min(620, max(460, int(total_width * 0.58)))
        if sizes[0] < minimum_editor or sizes[1] < 340:
            self.edit_splitter.setSizes([max(minimum_editor, desired_editor), desired_preview])

    def _save_window_state(self) -> None:
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("rootSplitter", self.root_splitter.saveState())
        self.settings.setValue("editSplitter", self.edit_splitter.saveState())

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._confirm_leave_editor():
            event.ignore()
            return
        self._save_window_state()
        event.accept()

    def _merge_method_tags(self, tags: list[str]) -> list[str]:
        seen: list[str] = []
        for tag in tags:
            if tag and tag not in seen:
                seen.append(tag)
        return seen

    def _get_question_by_id(self, question_id: str | None) -> Question | None:
        return next((question for question in self.questions if question.id == question_id), None)

    def _find_question_index(self, question_id: str | None) -> int | None:
        return next((index for index, question in enumerate(self.questions) if question.id == question_id), None)

    def persist_state(self) -> None:
        self.state.questions = self.questions
        self.state.method_tag_library = self.method_tag_library
        self.state.learning_tag_library = self.learning_tag_library
        self.state.baskets = self._valid_baskets(self.baskets)
        self.state.active_basket_id = self.active_basket_id
        self.repository.save(self.state)
        self.statusBar().showMessage(f"已保存到 {self.data_file}", 2000)
