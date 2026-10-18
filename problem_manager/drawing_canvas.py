from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QInputDialog, QWidget


class DrawingCanvas(QWidget):
    """A lightweight diagram canvas for question illustrations."""

    def __init__(self, width: int = 1080, height: int = 720, parent=None) -> None:
        super().__init__(parent)
        self.image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        self.image.fill(Qt.white)
        self.setMinimumSize(760, 500)
        self.setCursor(Qt.CrossCursor)
        self.tool = "画笔"
        self.color = QColor("#18251f")
        self.pen_width = 3
        self._start = QPoint()
        self._last = QPoint()
        self._before_stroke: QImage | None = None
        self._history: list[QImage] = []

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setCursor(Qt.IBeamCursor if tool == "文字" else Qt.CrossCursor)

    def set_pen_width(self, width: int) -> None:
        self.pen_width = width

    def set_color(self, color: QColor) -> None:
        if color.isValid():
            self.color = color

    def undo(self) -> None:
        if self._history:
            self.image = self._history.pop()
            self.update()

    def clear(self) -> None:
        self._push_history()
        self.image.fill(Qt.white)
        self.update()

    def export_image(self) -> QImage:
        return self.image.copy()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        point = self._image_point(event.position().toPoint())
        if self.tool == "文字":
            text, accepted = QInputDialog.getText(self, "添加文字", "请输入标注文字：")
            if accepted and text.strip():
                self._push_history()
                painter = QPainter(self.image)
                painter.setPen(QPen(self.color))
                painter.setFont(QFont("Times New Roman", max(12, self.pen_width * 6)))
                painter.drawText(point, text)
                painter.end()
                self.update()
            return
        self._push_history()
        self._start = point
        self._last = point
        self._before_stroke = self.image.copy()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if not event.buttons() & Qt.LeftButton or self._before_stroke is None:
            return
        point = self._image_point(event.position().toPoint())
        if self.tool == "画笔":
            painter = QPainter(self.image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(self._pen())
            painter.drawLine(self._last, point)
            painter.end()
            self._last = point
        else:
            self.image = self._before_stroke.copy()
            self._draw_shape(point)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._before_stroke is not None:
            if self.tool != "画笔":
                self._draw_shape(self._image_point(event.position().toPoint()))
            self._before_stroke = None
            self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eef2ed"))
        target = self._target_rect()
        painter.drawImage(target, self.image)
        painter.setPen(QPen(QColor("#9bad9e"), 1))
        painter.drawRect(target.adjusted(0, 0, -1, -1))

    def _draw_shape(self, endpoint: QPoint) -> None:
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self._pen())
        rectangle = QRect(self._start, endpoint).normalized()
        if self.tool == "直线":
            painter.drawLine(self._start, endpoint)
        elif self.tool == "矩形":
            painter.drawRect(rectangle)
        elif self.tool == "圆/椭圆":
            painter.drawEllipse(rectangle)
        painter.end()

    def _pen(self) -> QPen:
        return QPen(self.color, self.pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def _push_history(self) -> None:
        self._history.append(self.image.copy())
        if len(self._history) > 30:
            self._history.pop(0)

    def _target_rect(self) -> QRect:
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width = int(self.image.width() * scale)
        height = int(self.image.height() * scale)
        return QRect((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def _image_point(self, point: QPoint) -> QPoint:
        target = self._target_rect()
        x = max(0, min(self.image.width() - 1, int((point.x() - target.x()) * self.image.width() / target.width())))
        y = max(0, min(self.image.height() - 1, int((point.y() - target.y()) * self.image.height() / target.height())))
        return QPoint(x, y)
