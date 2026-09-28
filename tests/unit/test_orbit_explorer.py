from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402

from harness.config import Config  # noqa: E402
from harness.ui.orbit_explorer import (  # noqa: E402
    BLUR_FRONT,
    LENS_GROW,
    MAX_ENTRIES,
    MAX_SLOTS,
    PARENT_SCALE,
    OrbitExplorer,
    _counter_text,
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


def test_big_directory_scrolls_through_a_fixed_ring(qtbot, tmp_path):
    for i in range(80):
        (tmp_path / f"f{i:03d}.txt").write_text("x")
    big = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(big)
    ring = big.current
    assert ring.slots == MAX_SLOTS and ring.scrolls
    drawn = big.item_geometry(ring)
    assert len(drawn) <= MAX_SLOTS + 1  # a window onto the directory, not all 81 entries
    assert _counter_text(ring) == "1 / 80"
    for _ in range(30):
        big.step(1)
    big.settle()
    assert ring.entries[ring.front_index()].name == "f030.txt"
    assert _counter_text(ring) == "31 / 80"
    assert abs(math.sin(ring.angle_of(ring.front_index())) - 1.0) < 1e-6
    big.step(-32)  # past ".." wraps round to the end
    big.settle()
    assert ring.entries[ring.front_index()].name == "f079.txt"


@pytest.mark.parametrize("size", [(320, 400), (300, 180), (700, 300), (220, 500)])
def test_ring_and_front_label_fit_the_widget(qtbot, tmp_path, size):
    for i in range(80):
        (tmp_path / f"a_rather_long_file_name_{i:03d}.txt").write_text("x")
    widget = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(widget)
    widget.resize(*size)
    widget.settle()
    bounds = QRectF(0, 0, *size)
    front = front_item(widget)
    assert bounds.contains(widget._label_rect(front, is_front=True).center())
    assert widget._label_rect(front, is_front=True).bottom() <= size[1]
    for item in widget.item_geometry(widget.current):
        if item.visibility >= 0.5:
            assert -item.size / 2 <= item.pos.x() <= size[0] + item.size / 2
            assert 0 <= item.pos.y() <= size[1]


def test_type_ahead_jumps_and_repeats_cycle(qtbot, tmp_path):
    for name in ["alpha", "beta", "bravo", "charlie", "delta"]:
        (tmp_path / name).write_text("x")
    widget = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(widget)
    ring = widget.current

    def front_name():
        widget.settle()
        return ring.entries[ring.front_index()].name

    widget.type_ahead("c")
    assert front_name() == "charlie"
    widget.type_ahead("b")  # within the pause: "cb" matches nothing and is not a repeat
    assert front_name() == "charlie"
    widget._typed_at = 0.0  # a pause
    widget.type_ahead("b")
    assert front_name() == "beta"
    widget.type_ahead("r")
    assert front_name() == "bravo"
    widget._typed_at = 0.0
    widget.type_ahead("b")
    widget.type_ahead("b")  # repeated letter: next entry starting with it
    assert front_name() == "bravo"


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


def hover(widget, pos: QPointF):
    widget.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            pos,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_lens_magnifies_and_pushes_apart_without_moving_the_camera(explorer):
    ring = explorer.current
    explorer.mouse_roll = False
    before = {g.index: g for g in explorer.item_geometry(ring)}
    camera = QPointF(explorer.camera.center), explorer.camera.zoom
    front = front_item(explorer)
    hover(explorer, front.pos)
    assert explorer._hover_index == front.index
    explorer.settle()
    assert (explorer.camera.center, explorer.camera.zoom) == camera  # the lens, not the camera
    after = {g.index: g for g in explorer.item_geometry(ring)}
    assert after[front.index].size == pytest.approx(before[front.index].size * (1 + LENS_GROW))
    for index, item in after.items():
        if index != front.index and item.magnified > 0:
            # neighbours move away from the cursor
            old_d = math.dist(before[index].pos.toTuple(), front.pos.toTuple())
            assert math.dist(item.pos.toTuple(), front.pos.toTuple()) > old_d
    explorer.leaveEvent(None)
    explorer.settle()
    restored = {g.index: g for g in explorer.item_geometry(ring)}
    assert restored[front.index].size == pytest.approx(before[front.index].size)


def test_depth_of_field_blur(explorer):
    ring = explorer.current
    explorer.mouse_roll = False
    items = explorer.item_geometry(ring)
    front, back = max(items, key=lambda g: g.depth), min(items, key=lambda g: g.depth)
    assert front.blur == pytest.approx(0.0) and back.blur > 0.3  # no cursor: the back is soft
    hover(explorer, QPointF(5, explorer.height() - 5))  # far corner, over nothing
    explorer.settle()
    items = {g.index: g for g in explorer.item_geometry(ring)}
    others = [g for g in items.values() if g.index != front.index]
    assert min(g.blur for g in others) > 0.3  # far from the cursor: out of focus
    assert 0 < items[front.index].blur <= BLUR_FRONT  # the front softens only a little
    hover(explorer, front.pos)
    explorer.settle()
    assert {g.index: g for g in explorer.item_geometry(ring)}[front.index].blur == 0.0
    image = explorer.grab().toImage()  # the blurred icons render through the cache
    assert not image.isNull() and explorer._icons


def test_mouse_roll_at_the_sides(qtbot, tmp_path):
    for i in range(30):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    widget = OrbitExplorer(str(tmp_path), Config().ui.theme)
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    widget.settle()
    ring = widget.current
    start = ring.position
    hover(widget, QPointF(200, 40))  # the middle: no roll
    assert widget.mouse_roll_speed() == 0.0
    hover(widget, QPointF(398, 40))  # far right, above the ring: full speed
    assert widget.mouse_roll_speed() > 0
    right_neighbour = next(
        g for g in widget.item_geometry(ring) if g.index == (ring.front_index() - 1) % 31
    )
    assert right_neighbour.pos.x() > widget.width() / 2  # lower indices sit on the right
    for _ in range(30):
        widget._tick()
    assert ring.position < start - 1  # entries from the right came to the front
    hover(widget, QPointF(2, 40))
    assert widget.mouse_roll_speed() < 0
    widget.set_mouse_roll(False)
    assert widget.mouse_roll_speed() == 0.0
    widget.set_mouse_roll(True)
    widget.leaveEvent(None)
    assert widget.mouse_roll_speed() == 0.0
    widget.settle()
    assert abs(math.sin(ring.angle_of(ring.front_index())) - 1.0) < 1e-6  # snapped


def test_expand_flies_camera_and_collapse_flies_back(explorer, tree):
    root = explorer.current
    src = next(e for e in root.entries if e.name == "src")
    changed = []
    explorer.directory_changed.connect(changed.append)
    explorer.expand(src)
    assert explorer._flight is not None  # a flight is under way
    for _ in range(5):
        explorer._tick()
    child = explorer.current
    assert explorer.camera.zoom < explorer.fit_zoom(child)  # the camera pulls back mid-flight
    explorer.settle()
    assert child.path == str(tree / "src") and names(child) == ["..", "main.py", "util.py"]
    assert child.center != root.center  # the child lives somewhere else in the world
    assert explorer.camera.center == explorer._focus(child)
    assert explorer.camera.zoom == explorer.fit_zoom(child)
    assert root.scale == PARENT_SCALE and child.scale == 1.0
    assert changed == [str(tree / "src")]
    explorer.go_up()
    assert explorer._flight is not None
    explorer.settle()
    assert explorer.current is root and root.scale == 1.0 and len(explorer.rings) == 1
    assert explorer.camera.center == explorer._focus(root)


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
