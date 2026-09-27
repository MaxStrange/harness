from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402

from harness.config import Config  # noqa: E402
from harness.ui.orbit_explorer import (  # noqa: E402
    HOVER_ZOOM,
    MAX_ENTRIES,
    PARENT_SCALE,
    OrbitExplorer,
)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    (tmp_path / "src" / "util.py").write_text("x")
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    return tmp_path


@pytest.fixture
def explorer(qtbot, tree):
    widget = OrbitExplorer(str(tree), Config().ui.theme)
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    widget.settle()
    return widget


def names(ring):
    return [e.name for e in ring.entries]


def press_release(widget, pos: QPointF):
    args = (Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pos, *args))
    widget.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, pos, *args))


def front_item(explorer, ring=None):
    ring = ring or explorer.current
    return max(explorer.item_geometry(ring), key=lambda g: g.depth)


def test_root_ring_lists_parent_then_dirs_then_files_and_hides_dotfiles(explorer):
    ring = explorer.current
    assert names(ring) == ["..", "docs", "src", "README.md"]
    assert ring.entries[0].is_parent
    explorer.set_show_hidden(True)
    assert ".hidden" in names(explorer.current)


def test_front_item_and_rolling(explorer):
    ring = explorer.current
    assert ring.front_index() == 1  # the first real entry (after "..") starts at the front
    geometry = explorer.item_geometry(ring)
    front = max(geometry, key=lambda g: g.depth)
    back = min(geometry, key=lambda g: g.depth)
    assert front.entry.name == "docs" and front.size > back.size
    assert front.pos.y() > back.pos.y()  # the front is at the bottom of the flat ellipse
    explorer.step(1)
    explorer.settle()
    assert ring.front_index() == 2
    explorer.roll(-2 * math.pi / 4)
    assert ring.front_index() == 3


def test_radius_grows_with_entries(qtbot, tmp_path):
    small = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(small)
    for i in range(80):
        (tmp_path / f"f{i:03d}.txt").write_text("x")
    big = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(big)
    assert big.current.rx > small.current.rx * 4
    big.resize(300, 300)
    big.settle()
    # The ring is wider than the widget; most of it is off screen but the front is visible.
    front = front_item(big)
    assert 0 <= front.pos.x() <= 300
    assert any(g.pos.x() < 0 or g.pos.x() > 300 for g in big.item_geometry(big.current))


def test_drag_rolls_and_snaps(explorer):
    ring = explorer.current
    before = ring.rotation
    start = QPointF(200, 150)
    explorer.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    explorer.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(260, 150),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert explorer._dragging and ring.rotation != before
    explorer.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(260, 150),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    for _ in range(300):
        explorer._tick()
    assert ring.velocity == 0.0
    idx = ring.front_index()
    assert abs(math.sin(ring.angle_of(idx)) - 1.0) < 1e-3  # snapped


def test_hover_zooms_towards_item(explorer):
    ring = explorer.current
    item = front_item(explorer)
    explorer.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            item.pos,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert explorer._hover_index == item.index
    assert explorer.camera.target_zoom == HOVER_ZOOM
    assert explorer.camera.target_center.y() > ring.center.y()  # pulled towards the front item
    explorer.settle()
    assert front_item(explorer).size > item.size
    explorer.leaveEvent(None)
    explorer.settle()
    assert explorer.camera.zoom == 1.0 and explorer.camera.center == ring.center


def test_expand_flies_camera_and_collapse_flies_back(explorer, tree):
    root = explorer.current
    src = next(e for e in root.entries if e.name == "src")
    changed = []
    explorer.directory_changed.connect(changed.append)
    explorer.expand(src)
    assert explorer._flight is not None  # a flight is under way
    for _ in range(5):
        explorer._tick()
    assert explorer.camera.zoom < 1.0  # the camera pulls back mid-flight
    explorer.settle()
    child = explorer.current
    assert child.path == str(tree / "src") and names(child) == ["..", "main.py", "util.py"]
    assert child.center != root.center  # the child lives somewhere else in the world
    assert explorer.camera.center == child.center and explorer.camera.zoom == 1.0
    assert root.scale == PARENT_SCALE and child.scale == 1.0
    assert changed == [str(tree / "src")]
    explorer.go_up()
    assert explorer._flight is not None
    explorer.settle()
    assert explorer.current is root and root.scale == 1.0 and len(explorer.rings) == 1
    assert explorer.camera.center == root.center


def test_child_direction_follows_the_clicked_item(explorer, tree):
    root = explorer.current
    # "docs" is at the front (bottom): its children appear below the parent.
    explorer.expand(next(e for e in root.entries if e.name == "docs"))
    explorer.settle()
    assert explorer.current.center.y() > root.center.y()
    explorer.go_up()
    explorer.settle()
    # Put "src" at the left side of the ring, then expand: children appear to the left.
    src_index = names(root).index("src")
    root.rotation = math.pi - src_index * root.step
    explorer.expand(root.entries[src_index])
    explorer.settle()
    assert explorer.current.center.x() < root.center.x()


def test_click_dir_expands_file_selects_and_double_click_opens(explorer, tree):
    ring = explorer.current
    press_release(explorer, front_item(explorer).pos)  # "docs" at the front
    explorer.settle()
    assert explorer.current.path == str(tree / "docs")
    press_release(explorer, explorer.to_screen(ring.center))  # the shrunken parent ring
    explorer.settle()
    assert explorer.current is ring
    selected, opened = [], []
    explorer.file_selected.connect(selected.append)
    explorer.open_requested.connect(opened.append)
    readme = next(g for g in explorer.item_geometry(ring) if g.entry.name == "README.md")
    press_release(explorer, readme.pos)  # not at the front yet: comes to the front
    explorer.settle()
    assert front_item(explorer).entry.name == "README.md" and selected == []
    press_release(explorer, front_item(explorer).pos)  # at the front: selected, absolute path
    assert selected == [str(tree / "README.md")]
    explorer.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            front_item(explorer).pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert opened == [str(tree / "README.md")]


def test_reveal_walks_down_and_set_root(explorer, tree):
    explorer.reveal(str(tree / "src" / "util.py"))
    assert explorer.current.path == str(tree / "src")
    assert explorer.current.entries[explorer.current.front_index()].name == "util.py"
    explorer.reveal(str(tree / "docs"))
    assert explorer.current.path == str(tree / "docs")
    explorer.set_root(str(tree / "src"))
    assert explorer.root == str(tree / "src") and len(explorer.rings) == 1
    explorer.reveal(str(tree))  # outside the root: re-root instead of failing
    assert explorer.root == str(tree)


def test_parent_entry_goes_up_and_reroots_at_the_root(explorer, tree):
    root = explorer.current
    explorer.expand(next(e for e in root.entries if e.name == "src"))
    explorer.settle()
    parent = explorer.current.entries[0]
    assert parent.is_parent and parent.path == str(tree)
    explorer.expand(parent)
    explorer.settle()
    assert explorer.current is root and len(explorer.rings) == 1
    explorer.expand(root.entries[0])  # ".." at the root re-roots one level up
    explorer.settle()
    assert explorer.root == str(tree.parent)
    ring = explorer.current
    assert ring.entries[ring.front_index()].path == str(tree)


def test_large_directory_is_capped(qtbot, tmp_path):
    for i in range(MAX_ENTRIES + 25):
        (tmp_path / f"f{i:04d}.txt").write_text("x")
    widget = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(widget)
    assert len(widget.current.entries) == MAX_ENTRIES + 1 and widget.current.hidden_count == 25


def test_wheel_axes_and_touchpad_pixels(explorer):
    def wheel(angle: QPoint, pixels: QPoint | None = None):
        pixels = pixels or QPoint()
        return QWheelEvent(
            QPointF(100, 100),
            QPointF(100, 100),
            pixels,
            angle,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    ring = explorer.current
    start = ring.front_index()
    explorer.wheelEvent(wheel(QPoint(0, -120)))  # vertical notch: one step forward
    explorer.settle()
    assert ring.front_index() == (start + 1) % len(ring.entries)
    explorer.wheelEvent(wheel(QPoint(-120, 0)))  # horizontal (two-finger) swipe: one step back
    explorer.settle()
    assert ring.front_index() == start
    for _ in range(4):  # small touchpad pixel deltas accumulate into a step
        explorer.wheelEvent(wheel(QPoint(0, 0), QPoint(12, 0)))
    explorer.settle()
    assert ring.front_index() == (start + 1) % len(ring.entries)


def test_paints_offscreen(explorer, tree):
    explorer.expand(next(e for e in explorer.current.entries if e.name == "src"))
    for _ in range(10):
        explorer._tick()  # mid-flight: both rings drawn
    image = explorer.grab().toImage()
    assert not image.isNull() and image.width() == 400
