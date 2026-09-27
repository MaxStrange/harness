"""The viewer dock (UI5): a vertical strip of icons with macOS-style magnification.

Icons sit in the vertical middle. Moving the mouse over them magnifies the one
under the cursor and its neighbours, which slide outwards along a slight arc
towards the panel; on leaving, they ease back. The scale under the cursor is a
smooth bell so the whole strip flows rather than snapping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from harness.config import ThemeConfig

BASE_ICON = 36
MAX_SCALE = 1.6
INFLUENCE = 100.0  # px: how far the magnification reaches
GAP = 12.0
LEFT_MARGIN = 4.0
BULGE = 10.0  # px: how far a fully magnified icon slides towards the panel
ANIM_MS = 16
EASE = 0.28  # per tick, towards the target


@dataclass
class DockItem:
    name: str
    label: str
    glyph: str


class Dock(QWidget):
    item_clicked = Signal(str)

    def __init__(self, theme: ThemeConfig, width: int = 64) -> None:
        super().__init__()
        self.theme = theme
        self.items: list[DockItem] = []
        self.active: str | None = None
        self._mouse_y: float | None = None
        self._focus_y: float | None = None  # eased mouse position used for drawing
        self._intensity = 0.0  # 0 = resting, 1 = fully magnified
        self._target_intensity = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(ANIM_MS)
        self._timer.timeout.connect(self._animate)
        self.setFixedWidth(width)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)

    def add_item(self, name: str, label: str, glyph: str) -> None:
        self.items.append(DockItem(name, label, glyph))
        self.update()

    def set_active(self, name: str | None) -> None:
        self.active = name
        self.update()

    # -- geometry ----------------------------------------------------------

    def _resting_centers(self) -> list[float]:
        """Vertical centres with no magnification: the strip is centred in the widget."""
        count = len(self.items)
        total = count * BASE_ICON + max(0, count - 1) * GAP
        y = (self.height() - total) / 2
        return [y + BASE_ICON / 2 + i * (BASE_ICON + GAP) for i in range(count)]

    def _scales(self) -> list[float]:
        if self._focus_y is None or self._intensity <= 0.001:
            return [1.0] * len(self.items)
        scales = []
        for cy in self._resting_centers():
            distance = abs(cy - self._focus_y)
            bell = (
                0.5 * (1 + math.cos(math.pi * distance / INFLUENCE))
                if distance < INFLUENCE
                else 0.0
            )
            scales.append(1.0 + (MAX_SCALE - 1.0) * bell * self._intensity)
        return scales

    def _centers(self, scales: list[float]) -> list[float]:
        """Vertical centres once icons have grown: neighbours get pushed apart, the strip stays centred."""
        sizes = [BASE_ICON * s for s in scales]
        total = sum(sizes) + max(0, len(sizes) - 1) * GAP
        y = (self.height() - total) / 2
        centers = []
        for size in sizes:
            centers.append(y + size / 2)
            y += size + GAP
        return centers

    def _rects(self) -> list[QRectF]:
        scales = self._scales()
        centers = self._centers(scales)
        rects = []
        for scale, cy in zip(scales, centers, strict=True):
            size = BASE_ICON * scale
            bulge = BULGE * (scale - 1.0) / (MAX_SCALE - 1.0)  # the arc: bigger icons slide right
            cx = LEFT_MARGIN + BASE_ICON / 2 + bulge
            rects.append(QRectF(cx - size / 2, cy - size / 2, size, size))
        return rects

    def item_at(self, pos: QPointF) -> DockItem | None:
        for item, rect in zip(self.items, self._rects(), strict=True):
            if rect.adjusted(-6, -6, 6, 6).contains(pos):
                return item
        return None

    # -- animation -----------------------------------------------------------

    def _animate(self) -> None:
        target_y = self._mouse_y if self._mouse_y is not None else self._focus_y
        if target_y is not None:
            self._focus_y = (
                target_y
                if self._focus_y is None
                else self._focus_y + (target_y - self._focus_y) * 0.5
            )
        self._intensity += (self._target_intensity - self._intensity) * EASE
        settled = abs(self._target_intensity - self._intensity) < 0.01 and (
            target_y is None or self._focus_y is None or abs(target_y - self._focus_y) < 0.5
        )
        if settled:
            self._intensity = self._target_intensity
            if self._intensity == 0.0:
                self._focus_y = None
            self._timer.stop()
        self.update()

    def _set_target(self, intensity: float) -> None:
        self._target_intensity = intensity
        if not self._timer.isActive():
            self._timer.start()

    def settle(self) -> None:
        """Jump to the final state (tests, and anything that needs exact geometry now)."""
        self._intensity = self._target_intensity
        self._focus_y = self._mouse_y if self._target_intensity > 0 else None
        self._timer.stop()
        self.update()

    # -- events ------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._mouse_y = event.position().y()
        self._set_target(1.0)
        item = self.item_at(event.position())
        self.setToolTip(item.label if item else "")

    def enterEvent(self, event) -> None:
        self._set_target(1.0)

    def leaveEvent(self, event) -> None:
        self._mouse_y = None
        self._set_target(0.0)

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
        painter.end()
