from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import traceback


def run_self_test(root: Path, report_path: str) -> int:
    report = {'ok': False}
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from problem_manager.latex_exporter import export_questions_to_latex_bundle, LatexExportOptions
        from problem_manager.models import Question
        from problem_manager.preview import configure_formula_cache, _render_latex_svg_batch
        from problem_manager.repository import QuestionRepository

        app = QApplication.instance() or QApplication([])
        state = QuestionRepository(root / 'data' / 'questions.json').load()
        paths = {path for question in state.questions for path in question.all_images}
        if not state.questions or any(not Path(path).is_file() for path in paths):
            raise RuntimeError('Question data is empty or an image is missing.')
        report.update(questions=len(state.questions), images=len(paths))
        for tool in ('latex', 'dvisvgm', 'xelatex'):
            binary = shutil.which(tool)
            if not binary or not Path(binary).resolve().is_relative_to((root / 'runtime').resolve()):
                raise RuntimeError(f'Bundled tool not found: {tool}')
        with tempfile.TemporaryDirectory(prefix='math-manager-check-') as directory:
            scratch = Path(directory)
            configure_formula_cache(scratch / 'formula-cache')
            expression = r'\dfrac{1}{2}+\sqrt{x^2+1}+\begin{cases}x&x>0\\-x&x\leq0\end{cases}'
            rendered = _render_latex_svg_batch([expression])
            if expression not in rendered or 'data:image/svg+xml;base64,' not in rendered[expression]:
                raise RuntimeError('Bundled LaTeX formula rendering failed.')
            report['formula_rendering'] = True
            # Include the real bank plus a font/inline-formula check in the same export.
            questions = state.questions + [Question(stem=r'字体检查：宋体中文与 $\dfrac{1}{2}+x^2$。')]
            result = export_questions_to_latex_bundle(questions, scratch / 'check.tex', LatexExportOptions())
            document = QPdfDocument()
            if document.load(str(result.pdf_path)) != QPdfDocument.Error.None_ or document.pageCount() < 1:
                raise RuntimeError('Bundled PDF generation/loading failed.')
            report['pdf_pages'] = document.pageCount()
            document.close()
            view = QWebEngineView()
            loop = QEventLoop()
            loaded = []
            def check_images(ok):
                if not ok:
                    loaded.append(False)
                    loop.quit()
                    return
                view.page().runJavaScript('document.images.length > 0 && Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)', lambda value: (loaded.append(bool(value)), loop.quit()))
            view.loadFinished.connect(check_images)
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(30000)
            view.setHtml('<html><body>' + rendered[expression] + '</body></html>')
            loop.exec()
            timer.stop()
            view.close()
            if loaded != [True]:
                raise RuntimeError('Qt WebEngine formula display failed.')
            report['webengine'] = True
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
