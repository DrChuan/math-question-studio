from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from problem_manager.main_window import MainWindow
from problem_manager.repository import QuestionRepository
from problem_manager.ui_theme import APP_STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    root = Path(__file__).resolve().parent
    data_file = root / "data" / "questions.json"
    window = MainWindow(QuestionRepository(data_file), data_file)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
