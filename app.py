from __future__ import annotations

import sys
from multiprocessing import freeze_support

from PySide6.QtWidgets import QApplication

from problem_manager.main_window import MainWindow
from problem_manager.repository import QuestionRepository
from problem_manager.ui_theme import APP_STYLESHEET
from problem_manager.runtime import configure_runtime


def main() -> int:
    root = configure_runtime()
    if "--portable-self-test" in sys.argv:
        from problem_manager.portable_self_test import run_self_test
        return run_self_test(root, sys.argv[sys.argv.index("--portable-self-test") + 1])
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    data_file = root / "data" / "questions.json"
    window = MainWindow(QuestionRepository(data_file), data_file)
    window.show()
    return app.exec()


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
