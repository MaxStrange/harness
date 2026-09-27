from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402

from harness.config import Config  # noqa: E402
from harness.ui.critter import CRITTERS, DRAWERS, CritterWidget, Palette, StatusStrip  # noqa: E402


@pytest.mark.parametrize("kind", CRITTERS)
def test_every_critter_draws_busy_and_idle(qtbot, kind):
    pal = Palette(Config().ui.theme)
    for busy in (False, True):
        image = QImage(64, 44, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        DRAWERS[kind](painter, pal, 1.3, busy, False)
        painter.end()
        painted = sum(
            1
            for x in range(0, 64, 2)
            for y in range(0, 44, 2)
            if image.pixelColor(x, y).alpha() > 0
        )
        assert painted > 60, f"{kind} busy={busy} drew almost nothing"


def test_widget_animates_only_when_busy(qtbot):
    widget = CritterWidget(Config().ui.theme, "turtle")
    qtbot.addWidget(widget)
    phase = widget.phase
    widget._tick()
    assert widget.phase == phase
    widget.set_busy(True)
    widget._tick()
    assert widget.phase != phase
    widget.set_kind("nonsense")
    assert widget.kind == "lizard"


def test_status_strip(qtbot):
    strip = StatusStrip(Config().ui.theme, "songbird")
    qtbot.addWidget(strip)
    strip.set_busy(True)
    strip.set_status("Thinking...")
    assert strip.label.text() == "Thinking..." and strip.critter.busy
    strip.set_busy(False)
    assert strip.label.text() == "" and not strip.critter.busy
