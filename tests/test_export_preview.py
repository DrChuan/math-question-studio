"""Run with the conda base environment; exercises real XeLaTeX and Qt PDF."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from problem_manager.export_preview import ExportPreviewDialog
from problem_manager.image_markup import image_code, image_marker
from problem_manager.latex_exporter import LatexExportOptions
from problem_manager.models import Question
from problem_manager.ui_theme import APP_STYLESHEET


class ExportPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(APP_STYLESHEET)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="preview-test-")
        self.root = Path(self.temporary.name)
        self.paths = []
        for number, color in enumerate(("#dbe8df", "#f0dac4")):
            path = self.root / f"figure-{number}.png"
            image = QImage(320, 140, QImage.Format_RGB32)
            image.fill(QColor(color))
            image.save(str(path))
            self.paths.append(str(path))
        self.questions = [
            Question(stem=r"已知椭圆 $\dfrac{x^2}{4}+y^2=1$，求焦距。", question_type="解答题",
                     image_paths=[self.paths[0]], analysis="图片前。\n" + image_code(self.paths[1], 40) + "\n图片后。",
                     analysis_image_paths=[self.paths[1]]),
            Question(stem="长题：\n" + "\n".join(f"（{i}）验证 $x^2+y^2={i}$。" for i in range(1, 36)), question_type="解答题"),
            Question(stem="若 $x^2=9$，则 $x=$@。", question_type="填空题"),
            Question(stem="若 $y^2=16$，则 $y=$@。", question_type="填空题"),
        ]
        self.original = [q.to_dict() for q in self.questions]
        self.dialog = ExportPreviewDialog(self.questions, LatexExportOptions(include_analysis=True), "测试试题篮")
        self.dialog.show()

    def wait_until(self, condition, timeout=40):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(50)
        self.assertTrue(condition(), self.dialog.status.text() + "\n" + self.dialog.activity.toolTip())

    def ready(self):
        self.wait_until(lambda: self.dialog.confirm.isEnabled())
        self.assertEqual(len(self.dialog._locations), len(self.questions))

    def tearDown(self):
        self.dialog._timer.stop()
        self.wait_until(lambda: not self.dialog._running)
        self.dialog.reject()
        self.dialog.pdf_document.close()
        self.temporary.cleanup()

    def click_pdf_question(self, index, point=None, page=None):
        _, start_page, start_point = self.dialog._locations[index]
        page = start_page if page is None else page
        point = start_point + QPointF(35, 10) if point is None else point
        view = self.dialog.pdf_view
        view.scroll_to(page, point)
        QTest.qWait(50)
        before = view.verticalScrollBar().value()
        location = view.viewport_point(page, point)
        self.assertTrue(view.viewport().rect().contains(location.toPoint()))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=location.toPoint())
        self.assertEqual(self.dialog.question_list.currentRow(), index)
        self.assertEqual(before, view.verticalScrollBar().value(), "PDF clicks must not trigger a second scroll")

    def test_navigation_numbering_and_image_controls(self):
        d = self.dialog
        self.wait_until(lambda: d._running)
        self.assertTrue(d.progress.isVisible())
        self.assertIn("正在编译", d.activity_title.text())
        self.assertFalse(d.confirm.isEnabled())
        self.ready()
        self.assertFalse(d.progress.isVisible())
        self.assertEqual(d.pdf_view.verticalScrollBar().value(), 0)
        self.assertFalse(hasattr(d, "image_codes"))
        self.assertEqual(d.image_list.count(), 2)
        self.assertIn("题图 1", d.image_list.item(0).text())
        self.assertIn("解析图 1", d.image_list.item(1).text())
        for index in range(4):
            self.assertTrue(d.question_list.item(index).text().startswith(f"{index + 1}．"))
            QTest.mouseClick(d.question_list.viewport(), Qt.LeftButton, pos=d.question_list.visualItemRect(d.question_list.item(index)).center())
            self.assertGreater(d.pdf_view.verticalScrollBar().value(), 0)
        # Same-page, cross-page, continued long question and resized viewport hit testing.
        self.assertEqual(d._locations[2][1], d._locations[3][1])
        for index in (2, 3, 0, 1):
            self.click_pdf_question(index)
        self.assertLess(d._locations[1][1], d._locations[2][1])
        self.click_pdf_question(1, QPointF(120, 90), d._locations[1][1] + 1)
        d.resize(1040, 760)
        QTest.qWait(100)
        self.click_pdf_question(3)
        self.click_pdf_question(2)
        d.question_list.setCurrentRow(0)
        revision = d._revision
        d.image_list.setCurrentRow(1)
        self.assertEqual(d._revision, revision, "Selecting an image must not recompile")
        d.image_percent.setValue(53)
        self.assertEqual(d.image_scale.value(), 53)
        d.image_list.setCurrentRow(0)
        d.image_scale.setValue(62)
        self.assertEqual(d.image_percent.value(), 62)
        self.assertEqual(d._current_question().image_widths[image_marker(self.paths[1])], 53)
        self.assertFalse(d.confirm.isEnabled())
        d.gap_spin.setValue(12)
        d.request_render()
        # Make the running result obsolete, then check that only the latest result is shown.
        self.assertTrue(d._running)
        d.image_percent.setValue(58)
        self.ready()
        source = d._ready_tex.read_text()
        self.assertIn("width=0.58\\linewidth", source)
        self.assertIn("width=0.53\\linewidth", source)
        self.assertIn(r"\vspace{12.00mm}", source)
        saved_position = d.pdf_view.verticalScrollBar().value()
        d.request_render()
        self.ready()
        self.assertEqual(d.pdf_view.verticalScrollBar().value(), saved_position)
        d.question_list.setCurrentRow(3)
        moved_id = d._current_question().id
        d.move_selected(-1)
        self.assertEqual(d.question_list.item(2).data(Qt.UserRole), moved_id)
        for index in range(4):
            self.assertTrue(d.question_list.item(index).text().startswith(f"{index + 1}．"))
        self.ready()
        self.assertEqual(d._locations[2][0], moved_id)
        order = [q.id for q in d.ordered_questions()]
        # Internal drag-and-drop uses model row moves rather than the up/down buttons.
        model = d.question_list.model()
        self.assertTrue(model.moveRow(QModelIndex(), 0, QModelIndex(), 2))
        self.assertEqual(d.ordered_questions()[1].id, order[0])
        self.assertTrue(model.moveRow(QModelIndex(), 1, QModelIndex(), 0))
        self.assertEqual([q.id for q in d.ordered_questions()], order)
        self.ready()
        self.assertEqual([location[0] for location in d._locations], order)
        self.click_pdf_question(3)
        self.click_pdf_question(2)
        d.question_list.setCurrentRow(0)
        d.image_list.setCurrentRow(0)
        d._reset_image_size()
        self.assertNotIn(image_marker(self.paths[0]), d._current_question().image_widths)
        self.ready()
        result = d.save_to(self.root / "saved.tex")
        self.assertEqual(result.pdf_path.read_bytes(), d._ready_tex.with_suffix(".pdf").read_bytes())
        self.assertEqual(self.original, [q.to_dict() for q in self.questions])
        # An actual compile failure keeps the old PDF and can be corrected and retried.
        valid_stem = d.questions[moved_id].stem
        d.questions[moved_id].stem = r"$\notARealMathCommand$"
        d._mark_changed()
        d.request_render()
        self.wait_until(lambda: not d._running)
        self.assertIn("编译未完成", d.activity_title.text())
        self.assertFalse(d.confirm.isEnabled())
        self.assertTrue(d.update_button.isEnabled())
        self.assertFalse(d.progress.isVisible())
        d.questions[moved_id].stem = valid_stem
        d._mark_changed()
        d.request_render()
        self.ready()

    def test_analysis_images_hidden_when_not_exported(self):
        self.ready()
        self.dialog.reject()
        self.dialog = ExportPreviewDialog(self.questions, LatexExportOptions(include_analysis=False), "不导出解析")
        self.dialog.show()
        self.assertEqual(self.dialog.image_list.count(), 1)
        self.dialog.question_list.setCurrentRow(2)
        self.assertEqual(self.dialog.image_list.count(), 0)
        self.assertFalse(self.dialog.image_percent.isEnabled())
        self.assertIn("无导出插图", self.dialog.image_heading.text())
        self.ready()


if __name__ == "__main__":
    unittest.main()
