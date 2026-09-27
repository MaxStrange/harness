"""A small animated critter that shows when the model is working, with a status next to it.

Five presets (``ui.critter`` in the config): lizard, turtle, sloth, dinosaur,
songbird. Each is a few QPainter shapes; ``phase`` drives the animation while
the agent is busy, and a slow blink keeps it alive while idle.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from harness.config import ThemeConfig

CRITTERS = ("lizard", "turtle", "sloth", "dinosaur", "songbird")
FRAME_MS = 70
W, H = 64, 44


class Palette:
    def __init__(self, theme: ThemeConfig) -> None:
        self.body = QColor(theme.accent)
        self.body_dark = QColor(theme.accent).darker(135)
        self.body_light = QColor(theme.accent_hover)
        self.eye = QColor(theme.accent_text)
        self.eye_white = QColor(theme.text)
        self.branch = QColor(theme.text_muted)
        self.note = QColor(theme.text)


def _eye(p: QPainter, pal: Palette, x: float, y: float, r: float, blink: bool) -> None:
    if blink:
        p.setPen(QPen(pal.eye, 1.5))
        p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
        return
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.eye_white)
    p.drawEllipse(QPointF(x, y), r, r)
    p.setBrush(pal.eye)
    p.drawEllipse(QPointF(x + r * 0.25, y), r * 0.55, r * 0.55)


def draw_lizard(p: QPainter, pal: Palette, phase: float, busy: bool, blink: bool) -> None:
    wag = math.sin(phase) * (6 if busy else 0)
    step = math.sin(phase * 2) * (3 if busy else 0)
    p.setPen(Qt.PenStyle.NoPen)
    # tail
    tail = QPainterPath(QPointF(24, 27))
    tail.cubicTo(QPointF(12, 22 + wag), QPointF(6, 30 - wag), QPointF(2, 20 + wag * 1.5))
    p.setPen(QPen(pal.body_dark, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(tail)
    # legs
    p.setPen(QPen(pal.body_dark, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x, s in ((28, step), (40, -step)):
        p.drawLine(QPointF(x, 30), QPointF(x - 5 + s, 38))
        p.drawLine(QPointF(x + 4, 30), QPointF(x + 9 - s, 38))
    # body and head
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body)
    p.drawEllipse(QRectF(22, 20, 26, 14))
    p.drawEllipse(QRectF(43, 17, 16, 12))
    p.setBrush(pal.body_light)
    p.drawEllipse(QRectF(28, 22, 12, 5))
    _eye(p, pal, 53, 21, 2.2, blink)
    if busy and math.sin(phase * 3) > 0.8:  # tongue flick
        p.setPen(QPen(QColor("#e05a5a"), 1.5))
        p.drawLine(QPointF(59, 24), QPointF(63, 23))


def draw_turtle(p: QPainter, pal: Palette, phase: float, busy: bool, blink: bool) -> None:
    bob = math.sin(phase) * (2 if busy else 0)
    step = math.sin(phase * 2) * (3 if busy else 0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body_dark)
    for x, s in ((20, step), (38, -step)):  # legs
        p.drawEllipse(QRectF(x + s, 30, 8, 8))
    p.setBrush(pal.body)  # head
    p.drawEllipse(QRectF(44 + bob, 18, 14, 11))
    p.setBrush(pal.body_dark)  # shell
    p.drawChord(QRectF(12, 12, 36, 26), 0, 180 * 16)
    p.setPen(QPen(pal.body_light, 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(17, 15, 26, 20), 20 * 16, 140 * 16)
    p.drawLine(QPointF(24, 25), QPointF(24, 18))
    p.drawLine(QPointF(36, 25), QPointF(36, 18))
    _eye(p, pal, 53 + bob, 22, 2, blink)


def draw_sloth(p: QPainter, pal: Palette, phase: float, busy: bool, blink: bool) -> None:
    sway = math.sin(phase * 0.6) * (4 if busy else 0)
    p.setPen(QPen(pal.branch, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(4, 6), QPointF(60, 6))
    p.setPen(QPen(pal.body_dark, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(18, 7), QPointF(24 + sway, 22))  # arms
    p.drawLine(QPointF(46, 7), QPointF(40 + sway, 22))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body_dark)
    p.drawEllipse(QRectF(20 + sway, 18, 24, 22))  # body
    p.setBrush(pal.body)
    p.drawEllipse(QRectF(24 + sway, 10, 16, 15))  # head
    p.setBrush(pal.body_light)
    p.drawEllipse(QRectF(27 + sway, 15, 10, 6))  # face patch
    _eye(p, pal, 29 + sway, 17.5, 1.6, blink or not busy)  # sleepy when idle
    _eye(p, pal, 35 + sway, 17.5, 1.6, blink or not busy)
    p.setPen(QPen(pal.eye, 1.2))
    p.drawArc(QRectF(29 + sway, 19, 6, 4), 200 * 16, 140 * 16)  # smile


def draw_dinosaur(p: QPainter, pal: Palette, phase: float, busy: bool, blink: bool) -> None:
    bob = abs(math.sin(phase * 2)) * (3 if busy else 0)
    jaw = (math.sin(phase * 2) + 1) * (2 if busy else 0)
    step = math.sin(phase * 2) * (4 if busy else 0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body_dark)
    p.drawEllipse(QRectF(24 + step, 30, 9, 12))  # legs
    p.drawEllipse(QRectF(36 - step, 30, 9, 12))
    tail = QPainterPath(QPointF(28, 24 - bob))
    tail.cubicTo(QPointF(16, 20 - bob), QPointF(8, 30), QPointF(2, 26))
    tail.cubicTo(QPointF(10, 34), QPointF(20, 32), QPointF(30, 32 - bob))
    p.setBrush(pal.body)
    p.drawPath(tail)
    p.drawEllipse(QRectF(24, 16 - bob, 26, 20))  # body
    p.drawRoundedRect(QRectF(40, 6 - bob, 20, 14), 5, 5)  # head
    p.setBrush(pal.body_light)
    p.drawEllipse(QRectF(30, 22 - bob, 14, 10))  # belly
    p.setPen(QPen(pal.body_dark, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(44, 22 - bob), QPointF(49, 25 - bob))  # tiny arm
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body_dark)
    p.drawRect(QRectF(48, 16 - bob + jaw, 12, 3))  # jaw
    _eye(p, pal, 52, 11 - bob, 2, blink)


def draw_songbird(p: QPainter, pal: Palette, phase: float, busy: bool, blink: bool) -> None:
    hop = abs(math.sin(phase * 2)) * (2 if busy else 0)
    beak = (math.sin(phase * 3) + 1) * (1.5 if busy else 0)
    p.setPen(QPen(pal.branch, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(6, 38), QPointF(58, 36))
    p.setPen(QPen(pal.body_dark, 1.5))
    p.drawLine(QPointF(28, 32 - hop), QPointF(27, 37))  # feet
    p.drawLine(QPointF(34, 32 - hop), QPointF(35, 37))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pal.body_dark)  # tail
    tail = QPainterPath(QPointF(22, 26 - hop))
    tail.lineTo(QPointF(8, 20 - hop))
    tail.lineTo(QPointF(10, 30 - hop))
    tail.closeSubpath()
    p.drawPath(tail)
    p.setBrush(pal.body)
    p.drawEllipse(QRectF(18, 18 - hop, 24, 16))  # body
    p.drawEllipse(QRectF(34, 10 - hop, 14, 13))  # head
    p.setBrush(pal.body_light)
    p.drawEllipse(QRectF(22, 24 - hop, 14, 8))  # wing
    p.setBrush(QColor("#e0a030"))  # beak
    p.drawPolygon(
        [QPointF(47, 16 - hop - beak), QPointF(56, 17 - hop), QPointF(47, 18 - hop + beak)]
    )
    _eye(p, pal, 42, 15 - hop, 1.8, blink)
    if busy:  # notes float up while it sings
        p.setPen(QPen(pal.note, 1.5))
        p.setBrush(pal.note)
        for k in range(2):
            t = (phase / (2 * math.pi) + k * 0.5) % 1.0
            x, y = 52 + k * 6 + math.sin(t * 6) * 2, 14 - t * 14
            p.drawEllipse(QPointF(x, y), 1.8, 1.4)
            p.drawLine(QPointF(x + 1.6, y), QPointF(x + 1.6, y - 6))


DRAWERS: dict[str, Callable[[QPainter, Palette, float, bool, bool], None]] = {
    "lizard": draw_lizard,
    "turtle": draw_turtle,
    "sloth": draw_sloth,
    "dinosaur": draw_dinosaur,
    "songbird": draw_songbird,
}


class CritterWidget(QWidget):
    def __init__(self, theme: ThemeConfig, kind: str = "lizard") -> None:
        super().__init__()
        self.palette_ = Palette(theme)
        self.kind = kind if kind in DRAWERS else "lizard"
        self.phase = 0.0
        self.busy = False
        self._ticks = 0
        self.setFixedSize(W, H)
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_kind(self, kind: str) -> None:
        self.kind = kind if kind in DRAWERS else "lizard"
        self.update()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.update()

    @property
    def blinking(self) -> bool:
        return self._ticks % 50 in (0, 1)  # a blink every ~3.5 s

    def _tick(self) -> None:
        self._ticks += 1
        if self.busy:
            self.phase = (self.phase + 0.35) % (2 * math.pi * 6)
            self.update()
        elif self._ticks % 50 in (0, 2):
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        DRAWERS[self.kind](painter, self.palette_, self.phase, self.busy, self.blinking)
        painter.end()


class StatusStrip(QWidget):
    """The critter and its status words, shown above the composer."""

    def __init__(self, theme: ThemeConfig, kind: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.critter = CritterWidget(theme, kind)
        layout.addWidget(self.critter)
        self.label = QLabel("")
        self.label.setObjectName("status")
        layout.addWidget(self.label, 1)

    def set_status(self, text: str, error: bool = False) -> None:
        self.label.setText(text)
        self.label.setObjectName("statusError" if error else "status")
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)

    def set_busy(self, busy: bool) -> None:
        self.critter.set_busy(busy)
        if not busy:
            self.set_status("")
