from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent  # noqa: E402

from harness.ui.viewers import image_viewer  # noqa: E402
from harness.ui.viewers.image_viewer import ImageViewer  # noqa: E402


@pytest.fixture
def viewer(qtbot):
    widget = ImageViewer()
    qtbot.addWidget(widget)
    widget.resize(500, 400)
    widget.show()
    return widget


def image(tmp_path, name, w=40, h=30):
    path = tmp_path / name
    QImage(w, h, QImage.Format.Format_RGB32).save(str(path))
    return str(path)


def names(viewer):
    return [viewer.tab_bar.tabText(i) for i in range(viewer.tab_bar.count())]


def test_each_image_gets_a_tab_and_reshowing_selects_it(viewer, tmp_path):
    assert viewer.tab_bar.isHidden() and viewer.title.text() == "No image"
    a, b, c = (image(tmp_path, n, 10 + i, 5) for i, n in enumerate(["a.png", "b.png", "c.png"]))
    for path in (a, b, c):
        assert viewer.show_image(path)
    assert names(viewer) == ["a.png", "b.png", "c.png"] and viewer.current_path() == c
    assert viewer.tab_bar.isVisible() and "[3/3]" in viewer.title.text()
    viewer.show_image(a)  # already open: no new tab, just selected
    assert names(viewer) == ["a.png", "b.png", "c.png"] and viewer.current_path() == a
    assert "10x5" in viewer.title.text() and "[1/3]" in viewer.title.text()


def test_flipping_with_keys_and_keeping_the_zoom(viewer, tmp_path):
    for n in ("a.png", "b.png", "c.png"):
        viewer.show_image(image(tmp_path, n, 400, 300))
    viewer.actual_size()
    viewer.zoom(2.0)  # 200%: both images get the same zoom when flipping
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    viewer.keyPressEvent(key)  # wraps round from the last tab
    assert viewer.current_path().endswith("a.png")
    assert viewer.label.pixmap().width() == 800
    viewer.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    )
    assert viewer.current_path().endswith("c.png") and viewer.label.pixmap().width() == 800
    viewer.step(-1)
    assert viewer.current_path().endswith("b.png")


def test_close_move_and_unreadable(viewer, tmp_path):
    a, b = image(tmp_path, "a.png"), image(tmp_path, "b.png")
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a png")
    viewer.show_image(a)
    viewer.show_image(b)
    assert not viewer.show_image(str(bad))
    assert "Cannot display broken.png" in viewer.title.text()
    viewer.tab_bar.moveTab(2, 0)
    assert [os.path.basename(t.path) for t in viewer.tabs] == ["broken.png", "a.png", "b.png"]
    viewer.close_other_tabs(1)
    assert names(viewer) == ["a.png"] and viewer.current_path() == a
    args = (
        Qt.MouseButton.MiddleButton,
        Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )
    centre = QPointF(viewer.tab_bar.tabRect(0).center())
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, centre, *args)
    assert viewer.eventFilter(viewer.tab_bar, release)  # middle click closes
    assert viewer.tabs == [] and viewer.tab_bar.isHidden() and viewer.title.text() == "No image"


def test_oldest_tab_closes_beyond_the_limit(viewer, tmp_path, monkeypatch):
    monkeypatch.setattr(image_viewer, "MAX_TABS", 3)
    paths = [image(tmp_path, f"{i}.png") for i in range(5)]
    for path in paths:
        viewer.show_image(path)
    assert names(viewer) == ["2.png", "3.png", "4.png"] and viewer.current_path() == paths[4]
