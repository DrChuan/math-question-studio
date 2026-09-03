from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from problem_manager.mathtype_support import MathTypeStatus
from problem_manager.latex_exporter import LatexExportOptions


class ExportOptionsDialog(QDialog):
    def __init__(self, math_type_status: MathTypeStatus, parent=None) -> None:
        super().__init__(parent)
        self.math_type_status = math_type_status
        self.setWindowTitle("导出选项")
        self.setMinimumWidth(560)
        self.resize(600, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        form = QHBoxLayout()

        self.title_edit = QLineEdit("高中数学题目")
        form.addWidget(QLabel("标题"))
        form.addWidget(self.title_edit, 1)
        layout.addLayout(form)

        content_group = QGroupBox("导出内容")
        content_layout = QVBoxLayout(content_group)
        self.include_analysis_checkbox = QCheckBox("导出答案和解析")
        self.include_analysis_checkbox.setChecked(False)
        content_layout.addWidget(self.include_analysis_checkbox)

        self.include_note_checkbox = QCheckBox("导出备注")
        self.include_note_checkbox.setChecked(False)
        content_layout.addWidget(self.include_note_checkbox)
        layout.addWidget(content_group)

        layout_group = QGroupBox("解答题版式")
        layout_group_layout = QVBoxLayout(layout_group)
        self.leave_solution_space_checkbox = QCheckBox("为解答题留答题空白")
        self.leave_solution_space_checkbox.setChecked(False)
        layout_group_layout.addWidget(self.leave_solution_space_checkbox)
        layout.addWidget(layout_group)

        hint = QLabel(
            "导出会统一改成行内公式、移除“第1题/题型”标题，并按试卷风格输出 .tex 与 .pdf。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5f5a50;")
        layout.addWidget(hint)

        math_label = QLabel(self._math_type_message())
        math_label.setWordWrap(True)
        math_label.setStyleSheet("color: #5f5a50;")
        layout.addWidget(math_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导出")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_options(self) -> LatexExportOptions:
        return LatexExportOptions(
            title=self.title_edit.text().strip() or "高中数学题目",
            include_analysis=self.include_analysis_checkbox.isChecked(),
            include_note=self.include_note_checkbox.isChecked(),
            leave_solution_space=self.leave_solution_space_checkbox.isChecked(),
            compile_pdf=True,
        )

    def _math_type_message(self) -> str:
        return "当前导出主路径已切换为 XeLaTeX 版式输出，会同时生成可编辑的 .tex 和排版后的 .pdf。"
