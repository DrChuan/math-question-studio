import math
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QWidget


class StarRating(QWidget):
    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 3.0
        self.setFixedSize(154, 34)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击星星左半边选半星，右半边选整星；方向键以半星调整")
        self.setAccessibleName("难度，半星至五星")

    def value(self):
        return self._value

    def setValue(self, value):
        value = min(5.0, max(0.5, round(float(value) * 2) / 2))
        if value != self._value:
            self._value = value
            self.update()
            self.valueChanged.emit(value)
        self.setAccessibleDescription(f"{value:g} 星")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(QFont("Arial Unicode MS", 23))
        for index in range(5):
            rect = QRectF(2 + index * 30, 0, 30, 32)
            painter.setPen(QColor("#d1d9d0"))
            painter.drawText(rect, Qt.AlignCenter, "★")
            fill = min(1.0, max(0.0, self._value - index))
            if fill:
                painter.save()
                painter.setClipRect(QRectF(rect.x(), rect.y(), rect.width() * fill, rect.height()))
                painter.setPen(QColor("#a86b2d"))
                painter.drawText(rect, Qt.AlignCenter, "★")
                painter.restore()
        if self.hasFocus():
            painter.setPen(QColor("#5d927e"))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)

    def mousePressEvent(self, event):
        self.setValue(math.ceil(max(0.0, event.position().x() - 2) / 15) / 2)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Down, Qt.Key_Right, Qt.Key_Up):
            self.setValue(self._value + (0.5 if event.key() in (Qt.Key_Right, Qt.Key_Up) else -0.5))
        else:
            super().keyPressEvent(event)


def stars_html(value: float) -> str:
    value = min(5.0, max(0.5, float(value)))
    return f"<span title='{value:g} 星' aria-label='{value:g} 星' style='position:relative;display:inline-block;color:#d1d9d0;font-family:sans-serif;letter-spacing:1px'>★★★★★<span style='position:absolute;left:0;top:0;width:{value * 20:g}%;overflow:hidden;white-space:nowrap;color:#a86b2d'>★★★★★</span></span>"
