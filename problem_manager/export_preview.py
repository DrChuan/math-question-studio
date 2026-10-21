from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, QSizeF, QThreadPool, QTimer, Qt, Signal, QSignalBlocker, QElapsedTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget

from problem_manager.models import Question
from problem_manager.image_markup import image_marker, image_code_settings, replace_image_code
from problem_manager.latex_exporter import LatexExportOptions, LatexExportResult, export_questions_to_latex_bundle


class _RenderSignals(QObject):
    finished = Signal(int, str, str)


class _RenderJob(QRunnable):
    def __init__(self, revision, questions, options, path):
        super().__init__()
        self.revision, self.questions, self.options, self.path = revision, questions, options, path
        self.signals = _RenderSignals()

    def run(self):
        try:
            result = export_questions_to_latex_bundle(self.questions, self.path, self.options)
            path, error = str(result.tex_path), ""
        except Exception as exc:
            path, error = "", str(exc)
        try:
            self.signals.finished.emit(self.revision, path, error)
        except RuntimeError:
            pass


_EXPORT_POOL = QThreadPool()
_EXPORT_POOL.setMaxThreadCount(1)


class _QuestionPdfView(QPdfView):
    page_clicked = Signal(int, QPointF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.question_regions = []
        self._press_position = None

    def page_geometry(self):
        """Mirror Qt's multipage layout, including point-to-screen rounding."""
        document = self.document()
        if document is None or document.status() != QPdfDocument.Status.Ready:
            return []
        margins = self.documentMargins()
        dpi_scale = QGuiApplication.primaryScreen().logicalDotsPerInch() / 72
        width = max(1, self.viewport().width() - margins.left() - margins.right())
        sizes = []
        for page in range(document.pageCount()):
            base = QSizeF(document.pagePointSize(page) * dpi_scale).toSize()
            scale = width / base.width()
            size = QSizeF(base.width() * scale, base.height() * scale).toSize()
            sizes.append((size, dpi_scale * scale))
        total_width = max((size.width() for size, _ in sizes), default=0) + margins.left() + margins.right()
        y = margins.top()
        pages = []
        for size, scale in sizes:
            x = int((max(total_width, self.viewport().width()) - size.width()) / 2)
            pages.append((QRectF(x, y, size.width(), size.height()), scale))
            y += size.height() + self.pageSpacing()
        return pages

    def viewport_point(self, page, point):
        pages = self.page_geometry()
        if not 0 <= page < len(pages):
            return QPointF()
        rect, scale = pages[page]
        return rect.topLeft() + point * scale - QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def scroll_to(self, page, point):
        pages = self.page_geometry()
        if not 0 <= page < len(pages):
            return
        rect, scale = pages[page]
        self.verticalScrollBar().setValue(round(rect.top() + point.y() * scale - 24))

    def mousePressEvent(self, event):
        self._press_position = event.position() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press, self._press_position = self._press_position, None
        if press is not None and event.button() == Qt.LeftButton and (event.position() - press).manhattanLength() < 5:
            point = event.position() + QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            for page, (rect, scale) in enumerate(self.page_geometry()):
                if rect.contains(point):
                    self.page_clicked.emit(page, (point - rect.topLeft()) / scale)
                    break
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#158873"))
        pages = self.page_geometry()
        for page, top, bottom in self.question_regions:
            if page < len(pages):
                rect, scale = pages[page]
                origin = rect.topLeft() - QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
                start = origin + QPointF(8, top) * scale
                end = origin + QPointF(8, bottom) * scale
                painter.drawRoundedRect(QRectF(start.x(), start.y(), 4, end.y() - start.y()), 2, 2)


class ExportPreviewDialog(QDialog):
    """Edit an export copy and show the exact compiled PDF that will be saved."""

    def __init__(self, questions, options: LatexExportOptions, basket_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"导出预览 · {basket_name}")
        self.setWindowModality(Qt.ApplicationModal)
        self.resize(1340, 920)
        self.questions = {question.id: Question.from_dict(question.to_dict()) for question in questions}
        for question in self.questions.values():
            question.image_widths.update({key: value for key, value in image_code_settings(question.analysis, question.analysis_image_paths).items() if value is not None})
        self.options = replace(options, compile_pdf=True, question_spacing_mm=dict(options.question_spacing_mm))
        self._temporary = tempfile.TemporaryDirectory(prefix="math-export-preview-")
        self._revision = 0
        self._render_serial = 0
        self._running = False
        self._closed = False
        self._dirty = True
        self._jobs = {}
        self._render_question_ids = []
        self._locations = []
        self._selecting_from_pdf = False
        self._pending_jump_id = None
        self._ready_tex: Path | None = None
        layout = QVBoxLayout(self)
        self.activity = QFrame()
        activity_layout = QHBoxLayout(self.activity)
        activity_text = QVBoxLayout()
        self.activity_title = QLabel("正在生成排版预览…")
        self.activity_title.setStyleSheet("font-weight: 700; font-size: 16px; background: transparent;")
        self.activity_detail = QLabel()
        self.activity_detail.setWordWrap(True)
        self.activity_detail.setStyleSheet("background: transparent;")
        activity_text.addWidget(self.activity_title)
        activity_text.addWidget(self.activity_detail)
        activity_layout.addLayout(activity_text, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(150, 10)
        activity_layout.addWidget(self.progress)
        layout.addWidget(self.activity)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        controls = QVBoxLayout(left)
        controls.setContentsMargins(0, 0, 6, 0)
        controls.addWidget(QLabel("题目顺序 · 拖动调整"))
        self.question_list = QListWidget()
        self.question_list.setTextElideMode(Qt.ElideRight)
        self.question_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.question_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.question_list.setDefaultDropAction(Qt.MoveAction)
        for question in questions:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, question.id)
            self.question_list.addItem(item)
        self._refresh_question_numbers()
        controls.addWidget(self.question_list, 1)
        move_row = QHBoxLayout()
        up = QPushButton("上移")
        down = QPushButton("下移")
        up.clicked.connect(lambda: self.move_selected(-1))
        down.clicked.connect(lambda: self.move_selected(1))
        move_row.addWidget(up)
        move_row.addWidget(down)
        controls.addLayout(move_row)
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("与下一题额外留白"))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0, 250)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSingleStep(5)
        self.gap_spin.setSuffix(" mm")
        gap_row.addWidget(self.gap_spin)
        controls.addLayout(gap_row)
        self.image_heading = QLabel("本题插图")
        controls.addWidget(self.image_heading)
        self.image_list = QListWidget()
        self.image_list.setIconSize(QSize(64, 44))
        self.image_list.setMinimumHeight(100)
        self.image_list.setMaximumHeight(185)
        controls.addWidget(self.image_list)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("宽度占正文"))
        self.image_scale = QSlider(Qt.Horizontal)
        self.image_scale.setRange(10, 100)
        size_row.addWidget(self.image_scale, 1)
        self.image_percent = QSpinBox()
        self.image_percent.setRange(10, 100)
        self.image_percent.setSuffix(" %")
        self.image_percent.setMinimumWidth(85)
        size_row.addWidget(self.image_percent)
        controls.addLayout(size_row)
        self.image_reset = QPushButton("恢复自动大小")
        self.image_reset.clicked.connect(self._reset_image_size)
        controls.addWidget(self.image_reset)
        self.update_button = QPushButton("更新预览")
        self.update_button.clicked.connect(self.request_render)
        controls.addWidget(self.update_button)
        splitter.addWidget(left)
        self.pdf_document = QPdfDocument(self)
        self.pdf_view = _QuestionPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.pdf_document)
        self.bookmarks.modelReset.connect(self._read_locations)
        splitter.addWidget(self.pdf_view)
        splitter.setSizes([340, 980])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.status = QLabel("正在生成排版预览…")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.confirm = QPushButton("确认导出")
        self.confirm.setObjectName("primaryButton")
        self.confirm.setStyleSheet("QPushButton#primaryButton:disabled { background: #dce5df; color: #84958b; border-color: #cbd8d0; }")
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(self.confirm)
        layout.addLayout(footer)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(650)
        self._timer.timeout.connect(self.request_render)
        self._elapsed = QElapsedTimer()
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(1000)
        self._activity_timer.timeout.connect(self._update_compile_time)
        self.question_list.currentRowChanged.connect(self._selection_changed)
        self.question_list.itemClicked.connect(lambda *_: self._jump_to_selected())
        self.question_list.model().rowsMoved.connect(lambda *_: self._order_changed())
        self.gap_spin.valueChanged.connect(self._gap_changed)
        self.image_list.currentRowChanged.connect(self._image_selection_changed)
        self.image_scale.valueChanged.connect(self._image_size_changed)
        self.image_percent.valueChanged.connect(self._image_size_changed)
        self.pdf_view.page_clicked.connect(self._pdf_clicked)
        self._selecting_from_pdf = True
        self.question_list.setCurrentRow(0)
        self._selecting_from_pdf = False
        self.finished.connect(self._finish)
        QTimer.singleShot(0, self.request_render)

    def ordered_questions(self):
        return [self.questions[self.question_list.item(row).data(Qt.UserRole)] for row in range(self.question_list.count())]

    def _current_question(self):
        item = self.question_list.currentItem()
        return self.questions[item.data(Qt.UserRole)] if item else None

    def _refresh_question_numbers(self):
        for row in range(self.question_list.count()):
            item = self.question_list.item(row)
            question = self.questions[item.data(Qt.UserRole)]
            summary = " ".join(question.stem.split())
            item.setText(f"{row + 1}．{question.question_type} | {summary[:35]}")
            item.setToolTip(f"第 {row + 1} 题 · {question.question_type}\n{question.stem}")

    def _selection_changed(self, *args):
        question = self._current_question()
        with QSignalBlocker(self.gap_spin), QSignalBlocker(self.image_list):
            self.gap_spin.setEnabled(question is not None and self.question_list.currentRow() < self.question_list.count() - 1)
            self.gap_spin.setValue(self.options.question_spacing_mm.get(question.id, 0) if question else 0)
            self.image_list.clear()
            if question:
                groups = [("题图", question.question_images)]
                if self.options.include_analysis:
                    groups.append(("解析图", question.analysis_image_paths))
                for group, paths in groups:
                    for number, path in enumerate(paths, 1):
                        item = QListWidgetItem()
                        item.setData(Qt.UserRole, path)
                        item.setData(Qt.UserRole + 1, f"{group} {number}")
                        pixmap = QPixmap(path)
                        if not pixmap.isNull():
                            item.setIcon(QIcon(pixmap.scaled(64, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                        item.setToolTip(Path(path).name)
                        self.image_list.addItem(item)
                        self._update_image_label(item)
            self.image_list.setCurrentRow(0 if self.image_list.count() else -1)
        self.image_heading.setText("本题插图" if self.image_list.count() else "本题无导出插图")
        self._image_selection_changed()
        self._highlight_selected()
        if not self._selecting_from_pdf:
            self._jump_to_selected()

    def _update_image_label(self, item):
        question = self._current_question()
        width = question.image_widths.get(image_marker(item.data(Qt.UserRole)))
        item.setText(f"{item.data(Qt.UserRole + 1)} · {str(width) + '%' if width is not None else '自动'}")

    def _image_selection_changed(self, *args):
        question = self._current_question()
        item = self.image_list.currentItem()
        for widget in (self.image_scale, self.image_percent, self.image_reset):
            widget.setEnabled(item is not None)
        width = question.image_widths.get(image_marker(item.data(Qt.UserRole)), 68) if item and question else 68
        with QSignalBlocker(self.image_scale), QSignalBlocker(self.image_percent):
            self.image_scale.setValue(width)
            self.image_percent.setValue(width)

    def _image_size_changed(self, value):
        question = self._current_question()
        item = self.image_list.currentItem()
        if question is None or item is None:
            return
        with QSignalBlocker(self.image_scale), QSignalBlocker(self.image_percent):
            self.image_scale.setValue(value)
            self.image_percent.setValue(value)
        path = item.data(Qt.UserRole)
        question.image_widths[image_marker(path)] = value
        question.analysis = replace_image_code(question.analysis, path, path, value)
        self._update_image_label(item)
        self._mark_changed()

    def _reset_image_size(self):
        question = self._current_question()
        item = self.image_list.currentItem()
        if question is None or item is None:
            return
        path = item.data(Qt.UserRole)
        question.image_widths.pop(image_marker(path), None)
        question.analysis = replace_image_code(question.analysis, path, path)
        self._update_image_label(item)
        self._image_selection_changed()
        self._mark_changed()

    def _read_locations(self):
        locations = []
        roles = QPdfBookmarkModel.Role
        for row, question_id in enumerate(self._render_question_ids):
            index = self.bookmarks.index(row, 0)
            page = self.bookmarks.data(index, int(roles.Page))
            point = self.bookmarks.data(index, int(roles.Location))
            if page is not None and point is not None:
                locations.append((question_id, page, QPointF(point)))
        self._locations = locations
        self._highlight_selected()

    def _jump_to_selected(self):
        question = self._current_question()
        if question is None:
            return
        for question_id, page, point in self._locations:
            if question_id == question.id:
                self.pdf_view.scroll_to(page, point)
                break
        if self._dirty:
            self._pending_jump_id = question.id

    def _pdf_clicked(self, page, point):
        selected_id = None
        for question_id, start_page, start in self._locations:
            if (start_page, start.y() - 3) <= (page, point.y()):
                selected_id = question_id
            else:
                break
        if selected_id is None:
            return
        self._selecting_from_pdf = True
        try:
            for row in range(self.question_list.count()):
                if self.question_list.item(row).data(Qt.UserRole) == selected_id:
                    self.question_list.setCurrentRow(row)
                    break
        finally:
            self._selecting_from_pdf = False
        if self._dirty:
            self._pending_jump_id = selected_id

    def _highlight_selected(self):
        question = self._current_question()
        self.pdf_view.question_regions = []
        for index, (question_id, page, point) in enumerate(self._locations):
            if question is None or question_id != question.id:
                continue
            next_location = self._locations[index + 1] if index + 1 < len(self._locations) else None
            last_page = next_location[1] if next_location else self.pdf_document.pageCount() - 1
            for current_page in range(page, last_page + 1):
                top = max(0, point.y() - 3) if current_page == page else 0
                bottom = max(0, next_location[2].y() - 5) if next_location and current_page == last_page else self.pdf_document.pagePointSize(current_page).height() - 15
                if bottom > top:
                    self.pdf_view.question_regions.append((current_page, top, bottom))
            break
        self.pdf_view.viewport().update()

    def move_selected(self, offset):
        row = self.question_list.currentRow()
        target = row + offset
        if not 0 <= target < self.question_list.count():
            return
        with QSignalBlocker(self.question_list):
            item = self.question_list.takeItem(row)
            self.question_list.insertItem(target, item)
            self.question_list.setCurrentRow(target)
        self._order_changed()

    def _order_changed(self):
        self._refresh_question_numbers()
        self._selection_changed()
        self._mark_changed()
        question = self._current_question()
        self._pending_jump_id = question.id if question else None

    def _gap_changed(self, value):
        question = self._current_question()
        if question:
            self.options.question_spacing_mm[question.id] = value
            self._mark_changed()

    def _mark_changed(self):
        self._revision += 1
        self._dirty = True
        self.confirm.setEnabled(False)
        self.confirm.setText("等待编译完成")
        self.status.setText("排版已修改，正在更新预览…")
        if not self._running:
            self._show_activity("排版已修改，准备重新编译", "右侧暂时保留上一次预览，编译完成后更新。", "pending")
        self._timer.start()

    def _show_activity(self, title, detail, state="busy"):
        colors = {"busy": ("#fff1d5", "#896020"), "pending": ("#fff1d5", "#896020"), "ready": ("#e1efe8", "#15614f"), "error": ("#fae5e1", "#9c322a")}
        background, foreground = colors[state]
        self.activity.setStyleSheet(f"QFrame {{ background: {background}; border: 1px solid {foreground}; border-radius: 8px; color: {foreground}; }} QLabel {{ border: none; }}")
        self.activity_title.setText(title)
        self.activity_detail.setText(detail)
        self.activity.setToolTip("")
        self.progress.setVisible(state == "busy")

    def _update_compile_time(self):
        self.activity_title.setText(f"正在编译排版 · 已用 {self._elapsed.elapsed() // 1000} 秒")

    def request_render(self):
        self._timer.stop()
        if self._closed or self._running:
            return
        self._running = True
        self._dirty = True
        self.confirm.setEnabled(False)
        self.confirm.setText("等待编译完成")
        self.update_button.setEnabled(False)
        self.update_button.setText("正在编译…")
        self.status.setText("正在排版…")
        self._elapsed.start()
        self._activity_timer.start()
        self._show_activity("正在编译排版 · 已用 0 秒", "正在生成真实 PDF；期间可继续调整，完成后自动更新预览。")
        revision = self._revision
        questions = [Question.from_dict(question.to_dict()) for question in self.ordered_questions()]
        self._compiling_question_ids = [question.id for question in questions]
        options = replace(self.options, question_spacing_mm=dict(self.options.question_spacing_mm))
        self._render_serial += 1
        path = Path(self._temporary.name) / f"render-{self._render_serial}" / "export.tex"
        job = _RenderJob(revision, questions, options, path)
        self._jobs[revision] = job
        job.signals.finished.connect(self._render_finished)
        _EXPORT_POOL.start(job)

    def _render_finished(self, revision, tex_path, error):
        self._jobs.pop(revision, None)
        self._running = False
        self._activity_timer.stop()
        if self._closed:
            return
        if revision != self._revision:
            QTimer.singleShot(0, self.request_render)
            return
        if error:
            self._render_failed(error)
            return
        tex = Path(tex_path)
        scroll_position = self.pdf_view.verticalScrollBar().value()
        self._render_question_ids = self._compiling_question_ids
        self.pdf_document.close()
        result = self.pdf_document.load(str(tex.with_suffix(".pdf")))
        if result != QPdfDocument.Error.None_:
            self._render_failed("PDF 预览加载失败，请重试。")
            return
        self._ready_tex = tex
        self._read_locations()
        self.pdf_view.verticalScrollBar().setValue(scroll_position)
        self._dirty = False
        if self._pending_jump_id:
            self._jump_to_selected()
            self._pending_jump_id = None
        self.confirm.setEnabled(True)
        self.confirm.setText("确认导出")
        self.update_button.setEnabled(True)
        self.update_button.setText("更新预览")
        self._show_activity("排版已更新", "点击左侧题目定位到 PDF；点击 PDF 中的题目可反选左侧列表。", "ready")
        self.status.setText(f"共 {len(self.questions)} 道题 · {self.pdf_document.pageCount()} 页")

    def _render_failed(self, error):
        self._show_activity("编译未完成，请重试", (error.splitlines() or ["未知错误"])[0][:160], "error")
        self.activity.setToolTip(error)
        self.confirm.setText("预览未就绪")
        self.status.setText("编译失败，未保存；请重新更新预览。")
        self.update_button.setEnabled(True)
        self.update_button.setText("重新编译")

    def accept(self):
        if not self._dirty and not self._running and self._ready_tex is not None:
            super().accept()

    def save_to(self, target):
        if self._dirty or self._running or self._ready_tex is None:
            raise RuntimeError("请先等待预览排版完成。")
        target = Path(target).with_suffix(".tex")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._ready_tex, target)
        shutil.copy2(self._ready_tex.with_suffix(".pdf"), target.with_suffix(".pdf"))
        return LatexExportResult(target, target.with_suffix(".pdf"))

    def _finish(self, result):
        self._closed = True
        self._timer.stop()
        self._activity_timer.stop()
