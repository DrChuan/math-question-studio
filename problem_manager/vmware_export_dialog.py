from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from problem_manager.vmware_automation import DEFAULT_GUEST_WORKDIR, DEFAULT_VMX_PATH, VmAutomationConfig
from problem_manager.word_exporter import WordExportOptions


class VmwareExportDialog(QDialog):
    def __init__(self, profile: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Win11 MathType 自动导出")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit("高中数学题目")
        form.addRow("标题", self.title_edit)

        self.include_analysis_checkbox = QCheckBox("导出解析")
        self.include_analysis_checkbox.setChecked(False)
        form.addRow("", self.include_analysis_checkbox)

        self.include_note_checkbox = QCheckBox("导出备注")
        self.include_note_checkbox.setChecked(False)
        form.addRow("", self.include_note_checkbox)

        self.vmx_path_edit = QLineEdit(profile.get("vmx_path", DEFAULT_VMX_PATH))
        form.addRow("VMX 路径", self.vmx_path_edit)

        self.vm_password_edit = QLineEdit()
        self.vm_password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("虚拟机密码", self.vm_password_edit)

        self.guest_username_edit = QLineEdit(profile.get("guest_username", ""))
        form.addRow("Windows 用户名", self.guest_username_edit)

        self.guest_password_edit = QLineEdit()
        self.guest_password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Windows 密码", self.guest_password_edit)

        self.guest_workdir_edit = QLineEdit(profile.get("guest_workdir", DEFAULT_GUEST_WORKDIR))
        form.addRow("Windows 工作目录", self.guest_workdir_edit)

        self.word_macro_name_edit = QLineEdit(profile.get("word_macro_name", ""))
        self.word_macro_name_edit.setPlaceholderText("可留空，程序会尝试常见 MathType 宏名")
        form.addRow("MathType 宏名", self.word_macro_name_edit)

        layout.addLayout(form)

        hint = QLabel(
            "流程：先在 macOS 导出带 LaTeX 原文的 Word，再自动送入 Win11 虚拟机，"
            "通过 Word/MathType 的“切换 TeX”处理后回传。\n"
            "如果留空宏名，程序会优先尝试内置的 TeX Toggle 自动化入口。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_word_options(self) -> WordExportOptions:
        return WordExportOptions(
            title=self.title_edit.text().strip() or "高中数学题目",
            include_analysis=self.include_analysis_checkbox.isChecked(),
            include_note=self.include_note_checkbox.isChecked(),
            prefer_mathtype=True,
        )

    def build_vmware_config(self) -> VmAutomationConfig:
        return VmAutomationConfig(
            vmx_path=self.vmx_path_edit.text().strip(),
            vm_password=self.vm_password_edit.text(),
            guest_username=self.guest_username_edit.text().strip(),
            guest_password=self.guest_password_edit.text(),
            guest_workdir=self.guest_workdir_edit.text().strip(),
            word_macro_name=self.word_macro_name_edit.text().strip(),
        )
