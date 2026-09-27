from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402

from harness.config import Config  # noqa: E402
from harness.ui.orbit_explorer import MAX_ENTRIES, OrbitExplorer  # noqa: E402


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


def test_hover_rolls_the_ring(explorer, qtbot):
    ring = explorer.current
    before = ring.rotation
    explorer._hovering = True
    explorer._hover_dx = 0.9
    for _ in range(5):
        explorer._tick()
    assert ring.rotation != before and ring.velocity < 0
    explorer._hovering = False
    for _ in range(200):
        explorer._tick()
    assert ring.velocity == 0.0
    # Snapped: some entry sits exactly at the front.
    idx = ring.front_index()
    assert abs(math.sin(ring.angle_of(idx)) - 1.0) < 1e-3


def test_expand_and_collapse(explorer, tree):
    root = explorer.current
    src = next(e for e in root.entries if e.name == "src")
    changed = []
    explorer.directory_changed.connect(changed.append)
    explorer.expand(src)
    explorer.settle()
    assert explorer.current.path == str(tree / "src")
    assert names(explorer.current) == ["..", "main.py", "util.py"]
    # The parent ring shrank and moved to the top row; the new one is full size in the middle.
    assert root.scale < 0.5 and root.center.y() < explorer.current.center.y()
    assert explorer.current.scale == 1.0
    assert changed == [str(tree / "src")]
    explorer.go_up()
    explorer.settle()
    assert explorer.current is root and root.scale == 1.0 and len(explorer.rings) == 1


def test_click_on_dir_expands_and_double_click_opens(explorer, tree, qtbot):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    ring = explorer.current
    front = max(explorer.item_geometry(ring), key=lambda g: g.depth)
    assert front.entry.name == "docs"
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        front.pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    explorer.mousePressEvent(press)
    explorer.settle()
    assert explorer.current.path == str(tree / "docs")
    # Clicking the shrunken parent ring goes back up.
    explorer.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            ring.center,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    explorer.settle()
    assert explorer.current is ring
    opened = []
    explorer.open_requested.connect(opened.append)
    explorer.bring_to_front(
        ring,
        ring.entries.index(next(e for e in ring.entries if e.name == "README.md")),
        animate=False,
    )
    front = max(explorer.item_geometry(ring), key=lambda g: g.depth)
    explorer.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            front.pos,
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
    # A path outside the root re-roots the explorer instead of failing.
    explorer.reveal(str(tree))
    assert explorer.root == str(tree)


def test_large_directory_is_capped(qtbot, tmp_path):
    for i in range(MAX_ENTRIES + 25):
        (tmp_path / f"f{i:04d}.txt").write_text("x")
    widget = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(widget)
    # The cap counts real entries; ".." rides on top of it.
    assert len(widget.current.entries) == MAX_ENTRIES + 1 and widget.current.hidden_count == 25


def test_item_at_prefers_front(explorer):
    ring = explorer.current
    front = max(explorer.item_geometry(ring), key=lambda g: g.depth)
    hit = explorer.item_at(QPointF(front.pos))
    assert hit is not None and hit[1].entry.name == front.entry.name
    assert explorer.item_at(QPointF(1, 1)) is None


def test_paints_offscreen(explorer, tree):
    explorer.expand(next(e for e in explorer.current.entries if e.name == "src"))
    explorer.settle()
    image = explorer.grab().toImage()
    assert not image.isNull() and image.width() == 400


def test_parent_entry_goes_up_and_reroots_at_the_root(explorer, tree):
    root = explorer.current
    explorer.expand(next(e for e in root.entries if e.name == "src"))
    explorer.settle()
    parent = explorer.current.entries[0]
    assert parent.is_parent and parent.path == str(tree)
    explorer.expand(parent)  # ".." inside src collapses back to the root ring
    explorer.settle()
    assert explorer.current is root and len(explorer.rings) == 1
    explorer.expand(root.entries[0])  # ".." at the root re-roots one level up ...
    explorer.settle()
    assert explorer.root == str(tree.parent)
    ring = explorer.current
    front = ring.entries[ring.front_index()]
    assert front.path == str(tree)  # ... with the old root at the front


def test_wheel_axes_and_touchpad_pixels(explorer):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

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
