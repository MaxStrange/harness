"""The viewer dock (UI5): a vertical strip of icons with macOS-style magnification."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from harness.config import ThemeConfig

BASE_ICON = 36
MAX_SCALE = 1.7
INFLUENCE = 110.0


@dataclass
class DockItem:
    name: str
    label: str
    glyph: str
    badge: int = 0


class Dock(QWidget):
    item_clicked = Signal(str)

    def __init__(self, theme: ThemeConfig, width: int = 64) -> None:
        super().__init__()
        self.theme = theme
        self.items: list[DockItem] = []
        self.active: str | None = None
        self._mouse_y: float | None = None
        self.setFixedWidth(width)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)

    def add_item(self, name: str, label: str, glyph: str) -> None:
        self.items.append(DockItem(name, label, glyph))
        self.update()

    def set_active(self, name: str | None) -> None:
        self.active = name
        self.update()

    def set_badge(self, name: str, count: int) -> None:
        for item in self.items:
            if item.name == name:
                item.badge = count
        self.update()

    # -- geometry ----------------------------------------------------------

    def _scales(self) -> list[float]:
        if self._mouse_y is None:
            return [1.0] * len(self.items)
        centers = self._centers([1.0] * len(self.items))
        scales = []
        for cy in centers:
            distance = abs(cy - self._mouse_y)
            factor = max(0.0, 1.0 - distance / INFLUENCE)
            scales.append(1.0 + (MAX_SCALE - 1.0) * factor * factor)
        return scales

    def _centers(self, scales: list[float]) -> list[float]:
        gap = 14.0
        y = 16.0
        centers = []
        for scale in scales:
            size = BASE_ICON * scale
            centers.append(y + size / 2)
            y += size + gap
        return centers

    def _rects(self) -> list[QRectF]:
        scales = self._scales()
        centers = self._centers(scales)
        rects = []
        for scale, cy in zip(scales, centers, strict=True):
            size = BASE_ICON * scale
            rects.append(QRectF(self.width() / 2 - size / 2, cy - size / 2, size, size))
        return rects

    def item_at(self, pos: QPointF) -> DockItem | None:
        for item, rect in zip(self.items, self._rects(), strict=True):
            if rect.adjusted(-6, -6, 6, 6).contains(pos):
                return item
        return None

    # -- events ------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._mouse_y = event.position().y()
        item = self.item_at(event.position())
        self.setToolTip(item.label if item else "")
        self.update()

    def leaveEvent(self, event) -> None:
        self._mouse_y = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        item = self.item_at(event.position())
        if item is not None:
            self.item_clicked.emit(item.name)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.theme.surface))
        painter.setPen(QPen(QColor(self.theme.border), 1))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        for item, rect in zip(self.items, self._rects(), strict=True):
            active = item.name == self.active
            path = QPainterPath()
            path.addRoundedRect(rect, rect.width() * 0.22, rect.width() * 0.22)
            painter.fillPath(path, QColor(self.theme.accent if active else self.theme.surface_alt))
            painter.setPen(QPen(QColor(self.theme.accent if active else self.theme.border), 1.5))
            painter.drawPath(path)
            font = QFont("monospace")
            font.setPixelSize(int(rect.height() * 0.45))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(self.theme.accent_text if active else self.theme.text))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, item.glyph)
            if item.badge:
                badge = QRectF(rect.right() - 10, rect.top() - 4, 16, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.theme.warning))
                painter.drawEllipse(badge)
                small = QFont()
                small.setPixelSize(10)
                painter.setFont(small)
                painter.setPen(QColor(self.theme.accent_text))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(min(item.badge, 9)))
        painter.end()
