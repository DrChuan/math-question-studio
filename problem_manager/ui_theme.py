from __future__ import annotations
from pathlib import Path


APP_STYLESHEET = """
QMainWindow {
    background: #eef1ed;
    color: #1d2924;
}

QWidget {
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    color: #1d2924;
}

QMenuBar {
    background: #173d36;
    color: #f9f5eb;
    padding: 5px 10px;
    border: none;
}

QMenuBar::item {
    background: transparent;
    padding: 7px 12px;
    border-radius: 5px;
}

QMenuBar::item:selected {
    background: #2c5b51;
}

QMenu {
    background: #fffdf8;
    border: 1px solid #d5dcd4;
    padding: 6px;
}

QMenu::item {
    padding: 7px 28px 7px 11px;
    border-radius: 4px;
}

QMenu::item:selected {
    background: #e7efe9;
    color: #123a31;
}

QStatusBar {
    background: #173d36;
    color: #eaf1ea;
    border: none;
    padding: 2px 10px;
}

QLabel#sidebarTitle {
    color: #173d36;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#sectionLabel {
    color: #8a4d26;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
    padding-top: 8px;
}

QLabel#pageTitle {
    color: #173d36;
    font-size: 19px;
    font-weight: 700;
}

QLabel#mutedText, QLabel#browseCount {
    color: #6d776f;
    font-size: 12px;
}

QLabel#activeFilters {
    color: #756858;
    font-size: 12px;
    background: #f5eee3;
    border: 1px solid #e4d5c1;
    border-radius: 6px;
    padding: 4px 8px;
}

QPushButton {
    background: #fffdf8;
    color: #244238;
    border: 1px solid #c7d2c9;
    border-radius: 6px;
    min-height: 26px;
    padding: 4px 12px;
}

QPushButton:hover {
    background: #edf4ee;
    border-color: #6f9483;
}

QPushButton:pressed {
    background: #dae8dc;
}

QPushButton:disabled {
    background: #e8ece7;
    color: #9aa49d;
    border-color: #d7ddd7;
}

QPushButton#primaryButton {
    background: #1d5a4e;
    color: white;
    border-color: #1d5a4e;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background: #174a40;
    border-color: #174a40;
}

QPushButton#secondaryButton {
    min-height: 22px;
    padding: 2px 10px;
    color: #5c6d63;
    background: #f5f8f3;
    border-color: #cfd9d0;
    font-size: 12px;
}

QPushButton#secondaryButton:hover {
    color: #1d5a4e;
    background: #e8f2ea;
}

QPushButton#basketButton {
    background: #fbf0df;
    border-color: #d9ad78;
    color: #7d421f;
    font-weight: 700;
}

QComboBox#basketCombo {
    background: #fbf0df;
    border-color: #d9ad78;
    color: #7d421f;
    font-weight: 700;
}

QLineEdit, QPlainTextEdit, QComboBox {
    background: #fffefa;
    border: 1px solid #c8d3ca;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #bed9ca;
    selection-color: #14261f;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 2px solid #5d927e;
    padding: 4px 7px;
}

QPlainTextEdit {
    font-family: "Menlo", "PingFang SC", monospace;
    line-height: 1.45;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border: none;
    border-left: 1px solid #d1dbd2;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background: #edf3ee;
}

QComboBox::down-arrow { width: 0px; height: 0px; }

QComboBox QAbstractItemView {
    background: #fffefa;
    border: 1px solid #c8d3ca;
    selection-background-color: #dcece0;
}

QGroupBox {
    background: #f8faf6;
    border: 1px solid #d8e0d8;
    border-radius: 9px;
    margin-top: 12px;
    padding: 14px 10px 10px;
    font-weight: 700;
    color: #2a5348;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 7px;
    left: 10px;
    color: #8a4d26;
}

QTreeWidget, QListWidget {
    background: #fffefa;
    alternate-background-color: #f5f8f4;
    border: 1px solid #d4ddd4;
    border-radius: 7px;
    outline: none;
    padding: 4px;
}

QTreeWidget::item, QListWidget::item {
    padding: 5px 4px;
    border-radius: 4px;
}

QTreeWidget::item:hover, QListWidget::item:hover {
    background: #edf4ee;
}

QTreeWidget::item:selected, QListWidget::item:selected {
    background: #d9e9dc;
    color: #173d36;
}

QScrollArea {
    border: none;
    background: transparent;
}

QSplitter::handle {
    background: #d4dcd4;
}

QSplitter::handle:hover {
    background: #8fb09e;
}

QSplitter::handle:horizontal {
    width: 5px;
    margin: 8px 1px;
    border-radius: 2px;
}

QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 3px;
}

QScrollBar::handle:vertical {
    background: #b7c7bc;
    min-height: 32px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #8eaa99;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QCheckBox {
    spacing: 7px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #aebdb2;
    border-radius: 3px;
    background: #fffefa;
}

QCheckBox::indicator:checked {
    background: #28735f;
    border-color: #28735f;
    image: url("__CHECKMARK__");
}

QDialog {
    background: #f7f9f5;
}

QDialog QLabel {
    color: #284138;
}

QDialogButtonBox {
    padding-top: 6px;
}
""".replace("__CHECKMARK__", (Path(__file__).parent / "assets" / "checkmark.svg").as_posix())
