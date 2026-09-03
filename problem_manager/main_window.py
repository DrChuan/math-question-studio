from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
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
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from problem_manager.export_dialog import ExportOptionsDialog
from problem_manager.latex_exporter import LatexExportError, export_questions_to_latex_bundle
from problem_manager.mathtype_support import detect_mathtype
from problem_manager.math_normalizer import normalize_mixed_latex_text
from problem_manager.models import CHOICE_TYPES, OPTION_LABELS, Question, QuestionType
from problem_manager.preview import render_browse_cards, render_question_preview
from problem_manager.repository import QuestionRepository
from problem_manager.tag_catalog import DEFAULT_METHOD_TAGS, KNOWLEDGE_TAG_TREE


class BrowseActionPage(QWebEnginePage):
    """Keeps card controls in HTML while routing their actions back into Qt."""

    action_requested = Signal(str, str)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # type: ignore[override]
        if url.scheme() == "pm":
            self.action_requested.emit(url.host(), url.path().lstrip("/"))
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


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
        self.image_directory = self.data_file.parent / "images"
        self.image_directory.mkdir(parents=True, exist_ok=True)
        self.state = repository.load()
        self.questions = self.state.questions
        self.method_tag_library = self._merge_method_tags(self.state.method_tag_library)
        self.basket_question_ids = self._valid_basket_ids(self.state.basket_question_ids)

        self.current_question_id: str | None = None
        self.editing_question_id: str | None = None
        self._is_new_draft = False
        self.editing_image_path = ""
        self._loading_question = False
        self._changing_browse_tags = False
        self.expanded_question_ids: set[str] = set()
        self.math_type_status = detect_mathtype()
        self.current_mode = "browse"
        self._pending_browse_scroll_y: int | None = None
        self._browse_render_generation = 0

        self.setWindowTitle("高中数学题目管理器")
        self.resize(1680, 980)
        self._build_menu()
        self._build_ui()
        self._populate_browse_tag_tree()
        self._populate_browse_method_filter_list()
        self._populate_edit_knowledge_tree()
        self._populate_method_tag_list([])
        self._refresh_browse_results()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        new_action = QAction("新建题目", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.add_question)
        file_menu.addAction(new_action)

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

        root_splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(root_splitter)
        root_splitter.addWidget(self._build_left_panel())
        root_splitter.addWidget(self._build_workspace_panel())
        root_splitter.setSizes([330, 1330])

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
        self.browse_method_list = QListWidget()
        self.browse_method_list.itemChanged.connect(self._on_browse_filter_changed)
        layout.addWidget(self.browse_method_list, 2)
        return panel

    def _build_workspace_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(self._build_browse_page())
        self.workspace_stack.addWidget(self._build_edit_page())
        layout.addWidget(self.workspace_stack)
        return panel

    def _build_browse_page(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索题干 / 答案 / 解析 / 备注 / 标签")
        self.search_edit.textChanged.connect(self._on_browse_filter_changed)
        controls.addWidget(self.search_edit, 1)

        self.question_type_filter_combo = QComboBox()
        self.question_type_filter_combo.addItem("全部题型", None)
        for item in QuestionType:
            self.question_type_filter_combo.addItem(item.value, item.value)
        self.question_type_filter_combo.currentIndexChanged.connect(self._on_browse_filter_changed)
        controls.addWidget(self.question_type_filter_combo)

        self.basket_button = QPushButton("试题篮（0）")
        self.basket_button.setObjectName("basketButton")
        self.basket_button.clicked.connect(self.open_basket)
        controls.addWidget(self.basket_button)
        self.export_basket_button = QPushButton("导出试题篮")
        self.export_basket_button.setObjectName("primaryButton")
        self.export_basket_button.clicked.connect(self.export_basket_questions)
        controls.addWidget(self.export_basket_button)
        layout.addLayout(controls)

        self.browse_count_label = QLabel()
        self.browse_count_label.setObjectName("browseCount")
        layout.addWidget(self.browse_count_label)
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
        header.addWidget(save_return_button)
        layout.addLayout(header)

        edit_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(edit_splitter, 1)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(12, 12, 12, 18)
        editor_layout.setSpacing(10)

        metadata_group = QGroupBox("题目信息")
        metadata_layout = QHBoxLayout(metadata_group)
        self.question_type_combo = QComboBox()
        self.question_type_combo.addItems([item.value for item in QuestionType])
        self.question_type_combo.currentTextChanged.connect(self._on_editor_changed)
        metadata_layout.addWidget(QLabel("题型"))
        metadata_layout.addWidget(self.question_type_combo)

        self.difficulty_combo = QComboBox()
        for value in range(1, 6):
            self.difficulty_combo.addItem(str(value), value)
        self.difficulty_combo.currentIndexChanged.connect(self._on_editor_changed)
        metadata_layout.addWidget(QLabel("难度"))
        metadata_layout.addWidget(self.difficulty_combo)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("可留空，例如：2025 年高考全国卷")
        self.source_edit.textChanged.connect(self._on_editor_changed)
        self.source_edit.setMinimumWidth(130)
        self.source_edit.setMaximumWidth(260)
        metadata_layout.addWidget(QLabel("来源"))
        metadata_layout.addWidget(self.source_edit, 1)

        self.created_at_label = QLabel()
        self.created_at_label.setObjectName("mutedText")
        self.created_at_label.setMinimumWidth(92)
        metadata_layout.addWidget(QLabel("创建日期"))
        metadata_layout.addWidget(self.created_at_label)
        editor_layout.addWidget(metadata_group)

        self.stem_edit = self._make_editor("支持 $...$；简单分式如 a/b 自动规范化；填空横线用 $\\blank$")
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
            editor.textChanged.connect(self._on_editor_changed)
            options_layout.addRow(f"{label}", editor)
            self.option_edits.append(editor)
        editor_layout.addWidget(self.options_group)

        self.answer_edit = self._make_editor("答案支持 LaTeX")
        self.answer_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "答案", self.answer_edit)
        self.analysis_edit = self._make_editor("解析支持 LaTeX")
        self.analysis_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "解析", self.analysis_edit)
        self.note_edit = self._make_editor("备注")
        self.note_edit.textChanged.connect(self._on_editor_changed)
        self._add_editor_section(editor_layout, "备注", self.note_edit)

        knowledge_group = QGroupBox("知识点标签")
        knowledge_layout = QVBoxLayout(knowledge_group)
        self.edit_knowledge_tree = QTreeWidget()
        self.edit_knowledge_tree.setHeaderHidden(True)
        self.edit_knowledge_tree.setMinimumHeight(420)
        self.edit_knowledge_tree.itemChanged.connect(self._on_editor_changed)
        knowledge_layout.addWidget(self.edit_knowledge_tree)
        editor_layout.addWidget(knowledge_group)

        method_group = QGroupBox("方法/思想标签")
        method_layout = QVBoxLayout(method_group)
        method_row = QHBoxLayout()
        add_method_button = QPushButton("新增方法标签")
        add_method_button.clicked.connect(self.add_method_tag)
        method_row.addWidget(add_method_button)
        method_row.addStretch(1)
        method_layout.addLayout(method_row)
        self.method_tag_list = QListWidget()
        self.method_tag_list.setMinimumHeight(260)
        self.method_tag_list.itemChanged.connect(self._on_editor_changed)
        method_layout.addWidget(self.method_tag_list)
        editor_layout.addWidget(method_group)

        image_group = QGroupBox("题目配图（可选）")
        image_layout = QVBoxLayout(image_group)
        self.image_drop_label = ImageDropLabel()
        self.image_drop_label.image_file_dropped.connect(self._store_image_file)
        self.image_drop_label.image_dropped.connect(self._store_qimage)
        self.image_drop_label.paste_requested.connect(self.paste_image_from_clipboard)
        image_layout.addWidget(self.image_drop_label)
        image_buttons = QHBoxLayout()
        paste_image_button = QPushButton("从剪贴板粘贴")
        paste_image_button.clicked.connect(self.paste_image_from_clipboard)
        choose_image_button = QPushButton("选择图片文件")
        choose_image_button.clicked.connect(self.choose_image_file)
        remove_image_button = QPushButton("移除配图")
        remove_image_button.clicked.connect(self.remove_question_image)
        image_buttons.addWidget(paste_image_button)
        image_buttons.addWidget(choose_image_button)
        image_buttons.addWidget(remove_image_button)
        image_layout.addLayout(image_buttons)
        editor_layout.addWidget(image_group)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor_container)
        edit_splitter.addWidget(editor_scroll)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.addWidget(QLabel("实时预览"))
        self.edit_preview = QWebEngineView()
        preview_layout.addWidget(self.edit_preview, 1)
        edit_splitter.addWidget(preview_panel)
        edit_splitter.setSizes([700, 650])
        return panel

    def _make_editor(self, placeholder: str) -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(110)
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return editor

    def _add_editor_section(self, layout: QVBoxLayout, title: str, editor: QPlainTextEdit) -> None:
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        layout.addWidget(label)
        layout.addWidget(editor)

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

    def _populate_edit_knowledge_tree(self) -> None:
        with QSignalBlocker(self.edit_knowledge_tree):
            self.edit_knowledge_tree.clear()
            for book, chapters in KNOWLEDGE_TAG_TREE.items():
                book_item = QTreeWidgetItem([book])
                self.edit_knowledge_tree.addTopLevelItem(book_item)
                for chapter, topics in chapters.items():
                    chapter_item = QTreeWidgetItem([chapter])
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
        self._refresh_browse_results()

    def _clear_browse_tag_filters(self) -> None:
        with QSignalBlocker(self.browse_tag_tree), QSignalBlocker(self.browse_method_list):
            self._set_all_tree_checks(self.browse_tag_tree.invisibleRootItem(), Qt.Unchecked)
            for row in range(self.browse_method_list.count()):
                self.browse_method_list.item(row).setCheckState(Qt.Unchecked)
        self._refresh_browse_results()

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

    def _filtered_questions(self) -> list[Question]:
        keyword = self.search_edit.text().strip().lower()
        question_type = self.question_type_filter_combo.currentData()
        knowledge_filters = self._selected_browse_tag_filters()
        method_filters = self._selected_browse_method_filters()
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
            result.append(question)
        return result

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
        self.browse_count_label.setText(f"当前显示 {len(filtered)} 道题，共 {len(self.questions)} 道题")
        self.basket_button.setText(f"试题篮（{len(self.basket_question_ids)}）")
        self.export_basket_button.setEnabled(bool(self.basket_question_ids))
        self._pending_browse_scroll_y = scroll_y
        self.browse_web.setHtml(
            render_browse_cards(filtered, set(self.basket_question_ids), self.expanded_question_ids),
            QUrl("https://preview.local/"),
        )

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
        elif action == "edit":
            self.edit_question(question_id)
        elif action == "delete":
            self.delete_question(question_id)

    def _set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.workspace_stack.setCurrentIndex(0 if mode == "browse" else 1)
        if mode == "browse":
            self._refresh_browse_results()

    def add_question(self) -> None:
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
        self.stem_edit.setFocus()

    def edit_question(self, question_id: str) -> None:
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
            self._update_image_drop_preview()
            self.stem_edit.setPlainText(question.stem)
            for editor, content in zip(self.option_edits, question.options):
                editor.setText(content)
            self.answer_edit.setPlainText(question.answer)
            self.analysis_edit.setPlainText(question.analysis)
            self.note_edit.setPlainText(question.note)
            self._set_checked_edit_knowledge_tags(question.knowledge_tags)
            self._populate_method_tag_list(question.method_tags)
            self._toggle_options_visibility(question.question_type)
        finally:
            self._loading_question = False
        self.update_edit_preview()

    def _clear_editor_fields(self) -> None:
        self.question_type_combo.setCurrentText(QuestionType.SINGLE.value)
        self.difficulty_combo.setCurrentIndex(2)
        self.source_edit.clear()
        self.created_at_label.setText(Question().created_at)
        self.editing_image_path = ""
        self._update_image_drop_preview()
        self.stem_edit.clear()
        for editor in self.option_edits:
            editor.clear()
        self.answer_edit.clear()
        self.analysis_edit.clear()
        self.note_edit.clear()
        self._set_checked_edit_knowledge_tags([])
        self._populate_method_tag_list([])
        self._toggle_options_visibility(QuestionType.SINGLE.value)

    def cancel_edit(self) -> None:
        self.editing_question_id = None
        self._is_new_draft = False
        self._set_mode("browse")

    def save_and_return_to_browse(self) -> None:
        if self.save_current_question():
            self.editing_question_id = None
            self._set_mode("browse")

    def _on_editor_changed(self, *args) -> None:
        if self._loading_question:
            return
        self._toggle_options_visibility(self.question_type_combo.currentText())
        self.update_edit_preview()

    def _toggle_options_visibility(self, question_type: str) -> None:
        self.options_group.setVisible(question_type in CHOICE_TYPES)

    def update_edit_preview(self) -> None:
        self.edit_preview.setHtml(render_question_preview(self._build_question_from_editor()), QUrl("https://preview.local/"))

    def _build_question_from_editor(self) -> Question:
        original = self._get_question_by_id(self.editing_question_id)
        question = Question.from_dict(original.to_dict()) if original else Question(id=self.editing_question_id or Question().id)
        question.question_type = self.question_type_combo.currentText()
        question.difficulty = int(self.difficulty_combo.currentData())
        question.source = self.source_edit.text().strip()
        question.created_at = self.created_at_label.text() or question.created_at
        question.image_path = self.editing_image_path
        question.stem = normalize_mixed_latex_text(self.stem_edit.toPlainText())
        question.options = [normalize_mixed_latex_text(editor.text()) for editor in self.option_edits]
        question.answer = normalize_mixed_latex_text(self.answer_edit.toPlainText())
        question.analysis = normalize_mixed_latex_text(self.analysis_edit.toPlainText())
        question.note = normalize_mixed_latex_text(self.note_edit.toPlainText())
        question.knowledge_tags = self._selected_edit_knowledge_tags()
        question.method_tags = self._selected_method_tags()
        return question

    def paste_image_from_clipboard(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "剪贴板中没有图片", "请先复制一张图片，或使用“选择图片文件”。")
            return
        self._store_qimage(image)

    def choose_image_file(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self,
            "选择题目配图",
            str(Path.home()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if source:
            self._store_image_file(source)

    def _store_image_file(self, source: str) -> None:
        image = QImage(source)
        if image.isNull():
            QMessageBox.warning(self, "无法读取图片", "请选择有效的图片文件。")
            return
        self._store_qimage(image)

    def _store_qimage(self, image: QImage) -> None:
        if image.isNull() or not self.editing_question_id:
            return
        target = self.image_directory / f"{self.editing_question_id}.png"
        if not image.save(str(target), "PNG"):
            QMessageBox.warning(self, "保存图片失败", "无法将图片保存到题库资源目录。")
            return
        self.editing_image_path = str(target.resolve())
        self._update_image_drop_preview()
        self._on_editor_changed()

    def remove_question_image(self) -> None:
        if not self.editing_image_path:
            return
        self.editing_image_path = ""
        self._update_image_drop_preview()
        self._on_editor_changed()

    def _update_image_drop_preview(self) -> None:
        pixmap = QPixmap(self.editing_image_path)
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
        question = self._build_question_from_editor()
        index = self._find_question_index(self.editing_question_id)
        if index is None:
            self.questions.append(question)
        else:
            self.questions[index] = question
        self.state.questions = self.questions
        self._is_new_draft = False
        self.persist_state()
        return True

    def _selected_edit_knowledge_tags(self) -> list[str]:
        selected: list[str] = []
        root = self.edit_knowledge_tree.invisibleRootItem()
        for book_index in range(root.childCount()):
            book_item = root.child(book_index)
            for chapter_index in range(book_item.childCount()):
                chapter_item = book_item.child(chapter_index)
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
                for chapter_index in range(book_item.childCount()):
                    chapter_item = book_item.child(chapter_index)
                    for topic_index in range(chapter_item.childCount()):
                        topic_item = chapter_item.child(topic_index)
                        topic_item.setCheckState(0, Qt.Checked if topic_item.data(0, Qt.UserRole) in selected else Qt.Unchecked)

    def _selected_method_tags(self) -> list[str]:
        return [
            self.method_tag_list.item(row).text()
            for row in range(self.method_tag_list.count())
            if self.method_tag_list.item(row).checkState() == Qt.Checked
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
        self.persist_state()
        self._on_editor_changed()

    def _toggle_basket_question(self, question_id: str) -> None:
        if question_id in self.basket_question_ids:
            self.basket_question_ids.remove(question_id)
        else:
            self.basket_question_ids.append(question_id)
        self.persist_state()
        self._refresh_browse_results(preserve_scroll=True)

    def open_basket(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("试题篮")
        dialog.resize(660, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"试题篮中共有 {len(self.basket_question_ids)} 道题。选中题目后可移出题目篮。"))
        question_list = QListWidget()
        question_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for index, question in enumerate(self._basket_questions(), start=1):
            item = QListWidgetItem(question.title(index))
            item.setData(Qt.UserRole, question.id)
            question_list.addItem(item)
        layout.addWidget(question_list, 1)
        buttons = QHBoxLayout()
        remove_button = QPushButton("移除选中题目")

        def remove_selected() -> None:
            selected = {item.data(Qt.UserRole) for item in question_list.selectedItems()}
            if not selected:
                return
            self.basket_question_ids = [question_id for question_id in self.basket_question_ids if question_id not in selected]
            self.persist_state()
            for row in sorted((question_list.row(item) for item in question_list.selectedItems()), reverse=True):
                question_list.takeItem(row)
            self._refresh_browse_results()

        remove_button.clicked.connect(remove_selected)
        export_button = QPushButton("导出题目篮")
        export_button.clicked.connect(lambda: (dialog.accept(), self.export_basket_questions()))
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(remove_button)
        buttons.addStretch(1)
        buttons.addWidget(export_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.exec()

    def export_basket_questions(self) -> None:
        questions = self._basket_questions()
        if not questions:
            QMessageBox.information(self, "试题篮为空", "请先从题目卡片中将题目加入试题篮。")
            return
        dialog = ExportOptionsDialog(self.math_type_status, self)
        if dialog.exec() == 0:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出 LaTeX 与 PDF",
            str(self.data_file.parent / "导出题目.tex"),
            "LaTeX 源文件 (*.tex)",
        )
        if not target:
            return
        try:
            result = export_questions_to_latex_bundle(questions, target, dialog.build_options())
        except LatexExportError as error:
            QMessageBox.critical(self, "导出失败", f"LaTeX / PDF 导出失败：\n{error}")
            return
        message = f"已导出试题篮中的 {len(questions)} 道题。\n\n.tex 文件：\n{result.tex_path}"
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
        self.basket_question_ids = [item for item in self.basket_question_ids if item != question_id]
        self.expanded_question_ids.discard(question_id)
        self.persist_state()
        self._refresh_browse_results()

    def _basket_questions(self) -> list[Question]:
        by_id = {question.id: question for question in self.questions}
        return [by_id[question_id] for question_id in self.basket_question_ids if question_id in by_id]

    def _valid_basket_ids(self, question_ids: list[str]) -> list[str]:
        valid = {question.id for question in self.questions}
        seen: set[str] = set()
        return [question_id for question_id in question_ids if question_id in valid and not (question_id in seen or seen.add(question_id))]

    def _merge_method_tags(self, tags: list[str]) -> list[str]:
        seen: list[str] = []
        for tag in [*DEFAULT_METHOD_TAGS, *tags]:
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
        self.state.basket_question_ids = self.basket_question_ids
        self.repository.save(self.state)
        self.statusBar().showMessage(f"已保存到 {self.data_file}", 2000)
