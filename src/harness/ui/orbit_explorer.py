"""The orbiting file explorer (UI3).

Each directory is a ring of icons seen almost edge-on: a wide, flat ellipse
where the item at the front is largest and brightest and the ones at the back
are small and dim. The ring's radius grows with the number of entries, so a
big directory is a big ring of which only part is on screen.

Rings live in a world; a camera looks at it. Expanding a directory puts the
children's ring somewhere off to the side of the parent (in the direction of
the clicked item) and flies the camera there, like hopping between star
systems; the parent shrinks and stays where it was, so going back is a flight
too. Hovering an item zooms the camera towards it. The wheel, touchpad scroll
gestures, a horizontal drag and the arrow keys roll the ring, which snaps to
the nearest item when it stops.

The widget exposes the same interface as the tree stub (``set_root``,
``reveal``, ``root``, ``open_requested``, ``cwd_requested``) so the main window
does not care which one is in use.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from harness.config import ThemeConfig

MAX_ENTRIES = 600  # beyond this a directory is counted, not drawn
TICK_MS = 16
EASE = 0.16  # per tick, for camera and ring transforms
RING_RATIO = 0.26  # ellipse height / width: "almost in plane"
ICON_FRONT = 44.0  # px, front icon size on a full-size ring at zoom 1
ICON_BACK_FACTOR = 0.42  # back icons are this fraction of the front size
ITEM_SPACING = 64.0  # px along the front of the ring per entry: sets the radius
PARENT_SCALE = 0.55  # a ring you came from shrinks to this
PARENT_ALPHA = 0.55
HOVER_ZOOM = 1.28  # camera zoom when an item is under the mouse
HOVER_PULL = 0.5  # how far the camera moves towards a hovered item (0..1)
FLIGHT_SECONDS = 0.55
FLIGHT_DIP = 0.28  # the camera zooms out this much mid-flight
WHEEL_STEP_DEGREES = 15.0  # one wheel notch = one item
PIXEL_STEP = 40.0  # touchpad pixel travel per item
DRAG_THRESHOLD = 5.0  # px before a press becomes a drag
PARENT_NAME = ".."


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    is_parent: bool = False  # the ".." entry: goes up instead of expanding


@dataclass
class Ring:
    path: str
    entries: list[Entry]
    hidden_count: int = 0
    center: QPointF = field(default_factory=QPointF)  # world coordinates
    rotation: float = 0.0  # radians; item i sits at rotation + i * step
    velocity: float = 0.0  # radians per second
    scale: float = 0.2
    alpha: float = 0.0
    target_scale: float = 1.0
    target_alpha: float = 1.0
    dying: bool = False

    @property
    def step(self) -> float:
        return 2 * math.pi / max(1, len(self.entries))

    @property
    def rx(self) -> float:
        """World radius: enough for every entry to have ITEM_SPACING along the front."""
        return max(120.0, len(self.entries) * ITEM_SPACING / (2 * math.pi))

    @property
    def ry(self) -> float:
        return self.rx * RING_RATIO

    def angle_of(self, index: int) -> float:
        return self.rotation + index * self.step

    def world_pos(self, index: int) -> QPointF:
        angle = self.angle_of(index)
        return QPointF(
            self.center.x() + self.rx * self.scale * math.cos(angle),
            self.center.y() + self.ry * self.scale * math.sin(angle),
        )

    def front_index(self) -> int | None:
        if not self.entries:
            return None
        return max(range(len(self.entries)), key=lambda i: math.sin(self.angle_of(i)))

    def rotation_to_front(self, index: int) -> float:
        """The rotation that puts ``index`` exactly at the front, nearest to the current one."""
        target = math.pi / 2 - index * self.step
        turns = round((self.rotation - target) / (2 * math.pi))
        return target + turns * 2 * math.pi


@dataclass
class ItemGeometry:
    entry: Entry
    index: int
    pos: QPointF  # screen
    size: float  # screen
    depth: float  # 0 = back, 1 = front


@dataclass
class Camera:
    center: QPointF = field(default_factory=QPointF)
    zoom: float = 1.0
    target_center: QPointF = field(default_factory=QPointF)
    target_zoom: float = 1.0


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class OrbitExplorer(QWidget):
    open_requested = Signal(str)
    cwd_requested = Signal(str)
    file_selected = Signal(str)  # a file at the front was clicked: its absolute path
    directory_changed = Signal(str)

    def __init__(self, root: str, theme: ThemeConfig, show_hidden: bool = False) -> None:
        super().__init__()
        self.theme = theme
        self.show_hidden = show_hidden
        self.rings: list[Ring] = []
        self.camera = Camera()
        self._root = root
        self._hover_index: int | None = None
        self._snap_target: float | None = None
        self._flight: tuple[QPointF, QPointF, float] | None = None  # from, to, progress
        self._wheel_accum = 0.0
        self._press_pos: QPointF | None = None
        self._drag_last_x: float | None = None
        self._dragging = False
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 160)
        self.set_root(root)

    # -- public interface (same as the tree stub) ------------------------------

    @property
    def root(self) -> str:
        return self._root

    @property
    def current(self) -> Ring | None:
        live = [r for r in self.rings if not r.dying]
        return live[-1] if live else None

    def current_path(self) -> str:
        ring = self.current
        return ring.path if ring else self._root

    def set_root(self, root: str) -> None:
        self._root = str(root)
        self.rings = []
        self._flight = None
        ring = self._push_ring(self._root, QPointF(0, 0))
        ring.scale, ring.alpha = 1.0, 1.0
        self.camera = Camera(QPointF(ring.center), 1.0, QPointF(ring.center), 1.0)
        self.update()

    def reveal(self, path: str) -> None:
        """Expand down to ``path`` (a directory shows its contents; a file comes to the front)."""
        target = Path(path)
        try:
            relative = target.resolve().relative_to(Path(self._root).resolve())
        except ValueError:
            self.set_root(str(target if target.is_dir() else target.parent))
            relative = Path()
        parts = list(relative.parts)
        self.collapse_to(0)
        for i, part in enumerate(parts):
            ring = self.current
            if ring is None:
                break
            entry = next((e for e in ring.entries if e.name == part and not e.is_parent), None)
            if entry is None:
                break
            if entry.is_dir and (i < len(parts) - 1 or target.is_dir()):
                self.expand(entry)
            else:
                self.bring_to_front(ring, ring.entries.index(entry), animate=False)
        self.settle()

    def set_show_hidden(self, show: bool) -> None:
        self.show_hidden = show
        self.refresh()

    def refresh(self) -> None:
        for ring in self.rings:
            ring.entries, ring.hidden_count = self._read_dir(ring.path)
        self.update()

    # -- rings ----------------------------------------------------------------------

    def _read_dir(self, path: str) -> tuple[list[Entry], int]:
        try:
            names = os.listdir(path)
        except OSError:
            return [], 0
        entries = []
        for name in names:
            if not self.show_hidden and name.startswith("."):
                continue
            full = os.path.join(path, name)
            entries.append(Entry(name, full, os.path.isdir(full)))
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        hidden = max(0, len(entries) - MAX_ENTRIES)
        entries = entries[:MAX_ENTRIES]
        parent = os.path.dirname(path.rstrip("/\\")) or path
        if parent != path:
            entries.insert(0, Entry(PARENT_NAME, parent, True, is_parent=True))
        return entries, hidden

    def _push_ring(self, path: str, center: QPointF) -> Ring:
        entries, hidden = self._read_dir(path)
        ring = Ring(path=path, entries=entries, hidden_count=hidden, center=QPointF(center))
        first = 1 if ring.entries and ring.entries[0].is_parent and len(ring.entries) > 1 else 0
        ring.rotation = math.pi / 2 - first * ring.step  # first real entry at the front
        self.rings.append(ring)
        self.directory_changed.emit(path)
        return ring

    def _child_center(self, parent: Ring, index: int, child_rx: float) -> QPointF:
        """Where a child ring goes: off in the direction of the clicked item."""
        angle = parent.angle_of(index)
        dx, dy = math.cos(angle), math.sin(angle)
        length = math.hypot(dx, dy) or 1.0
        dx, dy = dx / length, dy / length
        distance_x = parent.rx * PARENT_SCALE + child_rx + 90
        distance_y = parent.ry * PARENT_SCALE + child_rx * RING_RATIO + 150
        return QPointF(parent.center.x() + dx * distance_x, parent.center.y() + dy * distance_y)

    def expand(self, entry: Entry) -> None:
        if not entry.is_dir:
            return
        if entry.is_parent:
            self.go_up()
            return
        parent = self.current
        assert parent is not None
        index = next((i for i, e in enumerate(parent.entries) if e.path == entry.path), None)
        if index is None:
            index = parent.front_index() or 0
        probe_entries, _ = self._read_dir(entry.path)
        probe = Ring(entry.path, probe_entries)
        center = self._child_center(parent, index, probe.rx)
        parent.target_scale, parent.target_alpha = PARENT_SCALE, PARENT_ALPHA
        child = self._push_ring(entry.path, center)
        self._fly_to(child)

    def collapse_to(self, depth: int) -> None:
        """Make ring ``depth`` current; deeper rings fade out and the camera flies back."""
        live = [r for r in self.rings if not r.dying]
        if depth >= len(live):
            return
        for ring in live[depth + 1 :]:
            ring.dying = True
            ring.target_alpha = 0.0
            ring.target_scale = 0.2
        target = live[depth]
        target.target_scale, target.target_alpha = 1.0, 1.0
        self.directory_changed.emit(target.path)
        self._fly_to(target)

    def go_up(self) -> None:
        """Back to the parent ring; at the root, re-root to the parent directory."""
        live = [r for r in self.rings if not r.dying]
        if len(live) > 1:
            self.collapse_to(len(live) - 2)
            return
        old_root = self._root
        parent = os.path.dirname(old_root.rstrip("/\\")) or old_root
        if parent == old_root:
            return
        self.set_root(parent)
        ring = self.current
        if ring is not None:
            index = next((i for i, e in enumerate(ring.entries) if e.path == old_root), None)
            if index is not None:
                self.bring_to_front(ring, index, animate=False)
        self.update()

    # -- camera ------------------------------------------------------------------------

    def _fly_to(self, ring: Ring) -> None:
        self._hover_index = None
        self._flight = (QPointF(self.camera.center), QPointF(ring.center), 0.0)
        self.camera.target_center = QPointF(ring.center)
        self.camera.target_zoom = 1.0
        self._start()

    def to_screen(self, world: QPointF) -> QPointF:
        cam = self.camera
        return QPointF(
            (world.x() - cam.center.x()) * cam.zoom + self.width() / 2,
            (world.y() - cam.center.y()) * cam.zoom + self.height() * 0.55,
        )

    def _aim_camera(self) -> None:
        """Camera target from the hover state (not during a flight)."""
        ring = self.current
        if ring is None or self._flight is not None:
            return
        if self._hover_index is not None and self._hover_index < len(ring.entries):
            item = ring.world_pos(self._hover_index)
            self.camera.target_center = QPointF(
                ring.center.x() + (item.x() - ring.center.x()) * HOVER_PULL,
                ring.center.y() + (item.y() - ring.center.y()) * HOVER_PULL,
            )
            self.camera.target_zoom = HOVER_ZOOM
        else:
            self.camera.target_center = QPointF(ring.center)
            self.camera.target_zoom = 1.0

    # -- geometry -------------------------------------------------------------------

    def item_geometry(self, ring: Ring) -> list[ItemGeometry]:
        zoom = self.camera.zoom
        items = []
        for i, entry in enumerate(ring.entries):
            depth = (math.sin(ring.angle_of(i)) + 1) / 2
            size = (
                ICON_FRONT * ring.scale * zoom * (ICON_BACK_FACTOR + (1 - ICON_BACK_FACTOR) * depth)
            )
            items.append(ItemGeometry(entry, i, self.to_screen(ring.world_pos(i)), size, depth))
        return items

    def item_at(self, pos: QPointF) -> tuple[Ring, ItemGeometry] | None:
        """Front-most item under ``pos`` on the current ring."""
        ring = self.current
        if ring is None:
            return None
        for item in sorted(self.item_geometry(ring), key=lambda g: -g.depth):
            half = item.size / 2 + 4
            if abs(pos.x() - item.pos.x()) <= half and abs(pos.y() - item.pos.y()) <= half + 10:
                return ring, item
        return None

    def parent_ring_at(self, pos: QPointF) -> Ring | None:
        live = [r for r in self.rings if not r.dying]
        for ring in live[:-1]:
            if ring.alpha <= 0.05:
                continue
            centre = self.to_screen(ring.center)
            rx = ring.rx * ring.scale * self.camera.zoom
            ry = ring.ry * ring.scale * self.camera.zoom
            dx, dy = pos.x() - centre.x(), pos.y() - centre.y()
            if (dx / (rx + 20)) ** 2 + (dy / (ry + 30)) ** 2 <= 1:
                return ring
        return None

    # -- motion ---------------------------------------------------------------------

    def roll(self, delta: float) -> None:
        """Rotate the current ring by ``delta`` radians immediately."""
        ring = self.current
        if ring is not None:
            ring.rotation += delta
            ring.velocity = 0.0
            self._snap_target = None
            self._aim_camera()
            self.update()

    def step(self, direction: int) -> None:
        ring = self.current
        if ring is None or not ring.entries:
            return
        front = ring.front_index()
        assert front is not None
        self.bring_to_front(ring, (front + direction) % len(ring.entries))

    def bring_to_front(self, ring: Ring, index: int, animate: bool = True) -> None:
        target = ring.rotation_to_front(index)
        if animate:
            self._snap_target = target
            self._start()
        else:
            ring.rotation = target
            self._snap_target = None

    def _start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        dt = TICK_MS / 1000.0
        moving = False
        ring = self.current
        cam = self.camera
        # Rolling: momentum after a drag, then a snap so an item sits exactly at the front.
        if ring is not None:
            if not self._dragging:
                ring.velocity *= 0.86
                if abs(ring.velocity) < 0.05:
                    ring.velocity = 0.0
                    if self._snap_target is None and ring.entries:
                        front = ring.front_index()
                        assert front is not None
                        self._snap_target = ring.rotation_to_front(front)
            if ring.velocity:
                ring.rotation += ring.velocity * dt
                moving = True
            elif self._snap_target is not None:
                diff = self._snap_target - ring.rotation
                if abs(diff) < 0.002:
                    ring.rotation = self._snap_target
                    self._snap_target = None
                else:
                    ring.rotation += diff * 0.2
                    moving = True
        # Camera: a flight has its own curve; otherwise ease towards the hover target.
        if self._flight is not None:
            start, end, t = self._flight
            t = min(1.0, t + dt / FLIGHT_SECONDS)
            e = _smoothstep(t)
            cam.center = QPointF(
                start.x() + (end.x() - start.x()) * e, start.y() + (end.y() - start.y()) * e
            )
            cam.zoom = 1.0 - FLIGHT_DIP * math.sin(math.pi * t)
            self._flight = (start, end, t) if t < 1.0 else None
            if self._flight is None:
                cam.center, cam.zoom = QPointF(end), 1.0
                self._aim_camera()
            moving = True
        else:
            dx = cam.target_center.x() - cam.center.x()
            dy = cam.target_center.y() - cam.center.y()
            dz = cam.target_zoom - cam.zoom
            if abs(dx) > 0.3 or abs(dy) > 0.3 or abs(dz) > 0.002:
                cam.center = QPointF(cam.center.x() + dx * EASE, cam.center.y() + dy * EASE)
                cam.zoom += dz * EASE
                moving = True
            else:
                cam.center, cam.zoom = QPointF(cam.target_center), cam.target_zoom
        for r in self.rings:
            if abs(r.scale - r.target_scale) > 0.003 or abs(r.alpha - r.target_alpha) > 0.01:
                r.scale += (r.target_scale - r.scale) * EASE
                r.alpha += (r.target_alpha - r.alpha) * EASE
                moving = True
            else:
                r.scale, r.alpha = r.target_scale, r.target_alpha
        if any(r.dying and r.alpha < 0.02 for r in self.rings):
            self.rings = [r for r in self.rings if not (r.dying and r.alpha < 0.02)]
        if not moving:
            self._timer.stop()
        self.update()

    def settle(self) -> None:
        """Finish every animation now (tests, and reveal)."""
        for r in self.rings:
            r.scale, r.alpha, r.velocity = r.target_scale, r.target_alpha, 0.0
        self.rings = [r for r in self.rings if not r.dying]
        ring = self.current
        if ring is not None and self._snap_target is not None:
            ring.rotation = self._snap_target
            self._snap_target = None
        self._flight = None
        self._aim_camera()
        self.camera.center = QPointF(self.camera.target_center)
        self.camera.zoom = self.camera.target_zoom
        self._timer.stop()
        self.update()

    # -- events -----------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging and (pos - self._press_pos).manhattanLength() > DRAG_THRESHOLD:
                self._dragging = True
                self._hover_index = None
                self._aim_camera()
            if self._dragging and self._drag_last_x is not None:
                ring = self.current
                if ring is not None:
                    radius = max(1.0, ring.rx * ring.scale * self.camera.zoom)
                    delta = (pos.x() - self._drag_last_x) / radius
                    ring.rotation -= delta
                    ring.velocity = -delta / (TICK_MS / 1000.0) * 0.5
                    self._snap_target = None
                self._drag_last_x = pos.x()
                self._start()
            return
        hit = self.item_at(pos)
        new_index = hit[1].index if hit else None
        if new_index != self._hover_index:
            self._hover_index = new_index
            self._aim_camera()
            self._start()
        if hit:
            entry = hit[1].entry
            tip = entry.path if not entry.is_parent else "Go to the parent folder"
            if not entry.is_dir and hit[0].front_index() == hit[1].index:
                tip += "\n(click to put this path in the message; double-click to open)"
            self.setToolTip(tip)
        else:
            self.setToolTip("")

    def leaveEvent(self, event) -> None:
        self._hover_index = None
        self._aim_camera()
        self._start()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Mouse wheel (either axis) and touchpad scroll gestures roll the ring."""
        pixels = event.pixelDelta()
        angle = event.angleDelta()
        if not pixels.isNull():
            travel = pixels.x() if abs(pixels.x()) >= abs(pixels.y()) else -pixels.y()
            self._wheel_accum += travel / PIXEL_STEP
        else:
            travel = angle.x() if abs(angle.x()) >= abs(angle.y()) else -angle.y()
            self._wheel_accum += travel / 8.0 / WHEEL_STEP_DEGREES
        while self._wheel_accum >= 1.0:
            self._wheel_accum -= 1.0
            self.step(1)
        while self._wheel_accum <= -1.0:
            self._wheel_accum += 1.0
            self.step(-1)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._drag_last_x = event.position().x()
            self._dragging = False

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_drag = self._dragging
        self._press_pos, self._drag_last_x, self._dragging = None, None, False
        if was_drag:
            self._start()
            return
        self.click(event.position())

    def click(self, pos: QPointF) -> None:
        """A plain click: parent ring -> go back; directory -> expand; file -> front, then select."""
        parent = self.parent_ring_at(pos)
        if parent is not None:
            live = [r for r in self.rings if not r.dying]
            self.collapse_to(live.index(parent))
            return
        hit = self.item_at(pos)
        if hit is None:
            return
        ring, item = hit
        if item.entry.is_dir:
            self.expand(item.entry)
        elif ring.front_index() == item.index:
            self.file_selected.emit(os.path.abspath(item.entry.path))
        else:
            self.bring_to_front(ring, item.index)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        hit = self.item_at(event.position())
        if hit is not None and not hit[1].entry.is_dir:
            self.open_requested.emit(os.path.abspath(hit[1].entry.path))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        ring = self.current
        if key == Qt.Key.Key_Left:
            self.step(-1)
        elif key == Qt.Key.Key_Right:
            self.step(1)
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Up, Qt.Key.Key_Escape):
            self.go_up()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down) and ring is not None:
            front = ring.front_index()
            if front is not None:
                entry = ring.entries[front]
                if entry.is_dir:
                    self.expand(entry)
                elif key != Qt.Key.Key_Down:
                    self.open_requested.emit(os.path.abspath(entry.path))
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        pos = QPointF(event.pos())
        hit = self.item_at(pos)
        menu = QMenu(self)
        current = self.current_path()
        if hit is not None and hit[1].entry.is_parent:
            menu.addAction("Go to parent folder", self.go_up)
        elif hit is not None:
            entry = hit[1].entry
            directory = entry.path if entry.is_dir else os.path.dirname(entry.path)
            menu.addAction("Use as working directory", lambda: self.cwd_requested.emit(directory))
            if entry.is_dir:
                menu.addAction("Expand", lambda: self.expand(entry))
            else:
                menu.addAction(
                    "Open in editor", lambda: self.open_requested.emit(os.path.abspath(entry.path))
                )
                menu.addAction(
                    "Put path in message",
                    lambda: self.file_selected.emit(os.path.abspath(entry.path)),
                )
        else:
            menu.addAction(
                "Use this directory as working directory", lambda: self.cwd_requested.emit(current)
            )
        menu.addSeparator()
        hidden = menu.addAction("Show hidden files")
        hidden.setCheckable(True)
        hidden.setChecked(self.show_hidden)
        hidden.toggled.connect(self.set_show_hidden)
        menu.addAction("Refresh", self.refresh)
        menu.exec(event.globalPos())

    # -- labels -------------------------------------------------------------------------

    def _label_font(self, item: ItemGeometry, is_front: bool) -> QFont:
        font = QFont()
        font.setPointSizeF(max(6.5, self.font().pointSizeF() - 2 + 3 * item.depth))
        font.setBold(is_front)
        return font

    def _label_rect(self, item: ItemGeometry, is_front: bool) -> QRectF:
        width = 90 + 80 * item.depth if is_front else 56 + 40 * item.depth
        top = item.pos.y() + (item.size * 0.78 if is_front else item.size / 2) + 2
        height = QFontMetricsF(self._label_font(item, is_front)).height() + 2
        return QRectF(item.pos.x() - width / 2, top, width, height)

    # -- painting -----------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        t = self.theme
        painter.fillRect(self.rect(), QColor(t.surface))
        font = QFont()
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(QColor(t.text_muted))
        crumb = _shorten_path(self.current_path(), Path.home())
        painter.drawText(
            QRectF(8, 4, self.width() - 16, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetricsF(font).elidedText(crumb, Qt.TextElideMode.ElideMiddle, self.width() - 16),
        )
        current = self.current
        for ring in self.rings:
            if ring.alpha <= 0.01 or ring.scale <= 0.01:
                continue
            self._paint_ring(painter, ring, ring is current)
        painter.end()

    def _paint_ring(self, painter: QPainter, ring: Ring, is_current: bool) -> None:
        t = self.theme
        centre = self.to_screen(ring.center)
        rx = ring.rx * ring.scale * self.camera.zoom
        ry = ring.ry * ring.scale * self.camera.zoom
        visible = QRectF(centre.x() - rx, centre.y() - ry, 2 * rx, 2 * ry).adjusted(
            -80, -80, 80, 80
        )
        if not visible.intersects(QRectF(self.rect())):
            return
        painter.setOpacity(ring.alpha)
        pen = QPen(QColor(t.border), 1.0)
        pen.setStyle(Qt.PenStyle.SolidLine if is_current else Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, rx, ry)
        if not ring.entries:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(
                QRectF(centre.x() - rx, centre.y() - 10, 2 * rx, 20),
                Qt.AlignmentFlag.AlignCenter,
                "(empty)",
            )
        widget_rect = QRectF(self.rect()).adjusted(-60, -60, 60, 60)
        items = [
            g
            for g in sorted(self.item_geometry(ring), key=lambda g: g.depth)
            if widget_rect.contains(g.pos)
        ]
        front = ring.front_index()
        labelled: set[int] = set()
        taken: list[QRectF] = []
        if ring.scale > 0.7:
            for item in reversed(items):
                rect = self._label_rect(item, is_front=item.index == front)
                if any(rect.intersects(other) for other in taken):
                    continue
                taken.append(rect)
                labelled.add(item.index)
        for item in items:
            self._paint_item(
                painter, item, ring, is_front=item.index == front, label=item.index in labelled
            )
        if ring.hidden_count and is_current:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(
                QRectF(centre.x() - 200, centre.y() + ry + 40, 400, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"+{ring.hidden_count} more (use find_files or the terminal)",
            )
        if not is_current:
            painter.setPen(QColor(t.text_muted))
            font = QFont()
            font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
            painter.setFont(font)
            name = os.path.basename(ring.path.rstrip("/\\")) or ring.path
            painter.drawText(
                QRectF(centre.x() - 100, centre.y() + ry + 14, 200, 16),
                Qt.AlignmentFlag.AlignCenter,
                name,
            )
        painter.setOpacity(1.0)

    def _paint_item(
        self, painter: QPainter, item: ItemGeometry, ring: Ring, is_front: bool, label: bool
    ) -> None:
        t = self.theme
        size = item.size
        x, y = item.pos.x(), item.pos.y()
        dim = 0.35 + 0.65 * item.depth
        painter.setOpacity(ring.alpha * dim)
        hovered = ring is self.current and item.index == self._hover_index
        if item.entry.is_parent:
            color = QColor(t.accent_hover if (is_front or hovered) else t.text_muted)
            painter.setPen(QPen(color, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(x - size / 2, y - size * 0.32, size, size * 0.72), size * 0.1, size * 0.1
            )
            arrow = QPainterPath()
            arrow.moveTo(x, y - size * 0.22)
            arrow.lineTo(x - size * 0.22, y + size * 0.05)
            arrow.lineTo(x - size * 0.08, y + size * 0.05)
            arrow.lineTo(x - size * 0.08, y + size * 0.3)
            arrow.lineTo(x + size * 0.08, y + size * 0.3)
            arrow.lineTo(x + size * 0.08, y + size * 0.05)
            arrow.lineTo(x + size * 0.22, y + size * 0.05)
            arrow.closeSubpath()
            painter.fillPath(arrow, color)
        elif item.entry.is_dir:
            color = QColor(t.accent_hover if (is_front or hovered) else t.accent)
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(x - size / 2, y - size * 0.32, size, size * 0.72), size * 0.1, size * 0.1
            )
            path.addRoundedRect(
                QRectF(x - size / 2, y - size * 0.44, size * 0.45, size * 0.2),
                size * 0.06,
                size * 0.06,
            )
            painter.setPen(QPen(QColor(t.accent_text), 1.0))
            painter.fillPath(path, color)
        else:
            color = QColor(t.text if (is_front or hovered) else t.text_muted)
            w, h = size * 0.68, size * 0.86
            path = QPainterPath()
            fold = w * 0.3
            path.moveTo(x - w / 2, y - h / 2)
            path.lineTo(x + w / 2 - fold, y - h / 2)
            path.lineTo(x + w / 2, y - h / 2 + fold)
            path.lineTo(x + w / 2, y + h / 2)
            path.lineTo(x - w / 2, y + h / 2)
            path.closeSubpath()
            painter.fillPath(path, QColor(t.surface_alt))
            painter.setPen(QPen(color, 1.2))
            painter.drawPath(path)
            painter.setPen(QPen(color, 1.0))
            for i in range(3):
                ly = y - h / 2 + fold + 4 + i * (h - fold - 8) / 3
                painter.drawLine(QPointF(x - w / 2 + 4, ly), QPointF(x + w / 2 - 4, ly))
        if (is_front or hovered) and ring.scale > 0.7:
            painter.setPen(QPen(QColor(t.accent_hover), 2.0 if is_front else 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(item.pos, size * 0.78, size * 0.78)
        if label:
            font = self._label_font(item, is_front)
            painter.setFont(font)
            painter.setPen(QColor(t.text if is_front else t.text_muted))
            rect = self._label_rect(item, is_front)
            text = QFontMetricsF(font).elidedText(
                item.entry.name + ("/" if item.entry.is_dir else ""),
                Qt.TextElideMode.ElideMiddle,
                rect.width(),
            )
            painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        painter.setOpacity(ring.alpha)


def _shorten_path(path: str, home: Path) -> str:
    try:
        return "~/" + str(Path(path).relative_to(home)) if Path(path) != home else "~"
    except ValueError:
        return path
