"""The orbiting file explorer (UI3).

Each directory is a ring of icons seen almost edge-on: a wide, flat ellipse
where the item at the front is largest and brightest and the ones at the back
are small and dim. A ring has at most MAX_SLOTS places; a bigger directory
scrolls through them like a carousel, entries fading in and out at the back.
The camera zooms so the current ring, with the labels of its front row, fits
the widget whatever its size.

Rings live in a world; a camera looks at it. Expanding a directory puts the
children's ring somewhere off to the side of the parent (in the direction of
the clicked item) and flies the camera there, like hopping between star
systems; the parent shrinks and stays where it was, so going back is a flight
too. The wheel, touchpad scroll gestures, a horizontal drag and the arrow keys
roll the ring, which snaps to the nearest item when it stops; with mouse roll
on, holding the cursor towards either side rolls it too. Typing a name brings
the first match to the front.

The mouse is a lens: icons near the cursor grow and are pushed apart, and
icons blur with their distance from the cursor and towards the back of the
ring, like a camera's depth of field.

Credit: the design follows Screenvader, the Flash portfolio of Stephane
Bourez (FWA of the Day, 10 December 2005), and the lens and blur follow Adam
Shailer's (Adasha) study of it, https://www.adasha.com/lab/layouts/vader/.
No code is taken from either; this is a reimplementation of the idea in Qt.

The widget exposes the same interface as the tree stub (``set_root``,
``reveal``, ``root``, ``open_requested``, ``cwd_requested``) so the main window
does not care which one is in use.
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections import OrderedDict
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
    QPixmap,
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
ITEM_SPACING = 64.0  # px along the front of the ring per slot: sets the radius
MAX_SLOTS = 12  # places on a ring; bigger directories scroll through them
MIN_RX = 120.0  # world radius of a ring with only a few entries
CRUMB_HEIGHT = 24.0  # px reserved at the top for the path
MARGIN = 8.0  # px kept free around the fitted ring
LABEL_ROOM = 26.0  # px below the front icon for its label
MIN_FIT_ZOOM = 0.45  # the fitted zoom never goes below this; tiny widgets clip instead
TYPE_AHEAD_SECONDS = 1.0  # a pause this long starts a new type-to-jump prefix
PARENT_SCALE = 0.55  # a ring you came from shrinks to this
PARENT_ALPHA = 0.55
LENS_RADIUS = 150.0  # px: icons closer than this to the cursor are magnified and pushed
LENS_PUSH = 0.55  # at the cursor, an icon moves away by this fraction of its distance
LENS_GROW = 0.6  # at the cursor, an icon grows by this fraction
LENS_EASE = 0.2  # per tick, the lens fading in and out as the mouse enters and leaves
BLUR_MAX_PX = 5.0  # px of blur at full depth of field (at an icon size of ICON_FRONT)
BLUR_LEVELS = 5  # blurred copies of each icon kept in the cache
BLUR_BACK = 0.55  # blur at the very back of the ring
BLUR_START = 45.0  # px from the cursor where the focus starts to go soft
BLUR_RUNOFF = 140.0  # px over which it goes fully soft
BLUR_PARENT = 0.7  # rings you came from are out of focus
BLUR_CURSOR = 0.65  # most blur the cursor's distance alone causes
BLUR_FRONT = 0.25  # the front item (what Enter opens) never gets softer than this
ROLL_ZONE = 0.55  # mouse roll starts this far from the centre (fraction of the half-width)
ROLL_MAX = 5.0  # slots per second at the very edge
ROLL_OVER_ITEM = 0.3  # roll speed factor while the cursor is on an icon, so it can be clicked
ICON_CACHE_SIZE = 600  # rendered icons kept
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
    def slots(self) -> int:
        return max(1, min(len(self.entries), MAX_SLOTS))

    @property
    def step(self) -> float:
        return 2 * math.pi / self.slots

    @property
    def scrolls(self) -> bool:
        """More entries than slots: the ring is a window onto the directory."""
        return len(self.entries) > self.slots

    @property
    def rx(self) -> float:
        """World radius: enough for every slot to have ITEM_SPACING along the front."""
        return max(MIN_RX, self.slots * ITEM_SPACING / (2 * math.pi))

    @property
    def ry(self) -> float:
        return self.rx * RING_RATIO

    @property
    def position(self) -> float:
        """The (fractional) entry index at the front."""
        return (math.pi / 2 - self.rotation) / self.step

    def offset(self, index: int) -> float:
        """How many slots ``index`` is from the front, wrapped to [-n/2, n/2)."""
        n = max(1, len(self.entries))
        return (index - self.position + n / 2) % n - n / 2

    def visibility(self, index: int) -> float:
        """1 on the ring, fading to 0 across the last slot before the back seam."""
        if not self.scrolls:
            return 1.0
        return max(0.0, min(1.0, self.slots / 2 - abs(self.offset(index))))

    def angle_of(self, index: int) -> float:
        return math.pi / 2 + self.offset(index) * self.step

    def world_pos(self, index: int) -> QPointF:
        angle = self.angle_of(index)
        return QPointF(
            self.center.x() + self.rx * self.scale * math.cos(angle),
            self.center.y() + self.ry * self.scale * math.sin(angle),
        )

    def front_index(self) -> int | None:
        if not self.entries:
            return None
        return round(self.position) % len(self.entries)

    def rotation_to_front(self, index: int) -> float:
        """The rotation that puts ``index`` exactly at the front, nearest to the current one."""
        target = math.pi / 2 - index * self.step
        period = max(1, len(self.entries)) * self.step  # 2 pi unless the ring scrolls
        turns = round((self.rotation - target) / period)
        return target + turns * period


@dataclass
class ItemGeometry:
    entry: Entry
    index: int
    pos: QPointF  # screen
    size: float  # screen
    depth: float  # 0 = back, 1 = front
    visibility: float = 1.0  # fades to 0 at the back seam of a scrolling ring
    blur: float = 0.0  # depth of field: 0 sharp .. 1 fully soft
    magnified: float = 0.0  # how strongly the lens acts on it: 0 .. 1


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
    reveal_requested = Signal(str)  # show this path in the system file manager
    directory_changed = Signal(str)

    mouse_roll_changed = Signal(bool)  # the user toggled mouse roll in the menu

    def __init__(
        self,
        root: str,
        theme: ThemeConfig,
        show_hidden: bool = False,
        mouse_roll: bool = True,
    ) -> None:
        super().__init__()
        self.theme = theme
        self.show_hidden = show_hidden
        self.mouse_roll = mouse_roll
        self._mouse: QPointF | None = None  # the cursor, while it is over the widget
        self._lens = 0.0  # current lens strength, eased towards 1 while the mouse is in
        self._icons: OrderedDict[tuple, QPixmap] = OrderedDict()
        self.rings: list[Ring] = []
        self.camera = Camera()
        self._root = root
        self._hover_index: int | None = None
        self._snap_target: float | None = None
        # from, to, zoom from, zoom to, progress
        self._flight: tuple[QPointF, QPointF, float, float, float] | None = None
        self._wheel_accum = 0.0
        self._press_pos: QPointF | None = None
        self._drag_last_x: float | None = None
        self._dragging = False
        self._typed = ""
        self._typed_at = 0.0
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
        focus, zoom = self._focus(ring), self.fit_zoom(ring)
        self.camera = Camera(QPointF(focus), zoom, QPointF(focus), zoom)
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
        cam = self.camera
        focus, zoom = self._focus(ring), self.fit_zoom(ring)
        self._flight = (QPointF(cam.center), QPointF(focus), cam.zoom, zoom, 0.0)
        cam.target_center, cam.target_zoom = QPointF(focus), zoom
        self._start()

    def _anchor_y(self) -> float:
        """Screen y of the camera centre: the middle of the area below the path."""
        return CRUMB_HEIGHT + (self.height() - CRUMB_HEIGHT) / 2

    @staticmethod
    def _extent(ring: Ring, zoom: float) -> tuple[float, float, float]:
        """Screen half-width, and height above and below the centre, of ``ring`` at ``zoom``."""
        half_width = (ring.rx + ICON_FRONT * 0.45) * zoom
        above = (ring.ry + ICON_FRONT * ICON_BACK_FACTOR * 0.6) * zoom
        below = (ring.ry + ICON_FRONT * 0.78) * zoom + LABEL_ROOM
        return half_width, above, below

    def fit_zoom(self, ring: Ring) -> float:
        """The largest zoom (at most 1) at which ``ring`` and its front labels fit the widget."""
        half_width, above, below = self._extent(ring, 1.0)
        width = self.width() - 2 * MARGIN
        height = self.height() - CRUMB_HEIGHT - 2 * MARGIN - LABEL_ROOM
        zoom = min(1.0, width / (2 * half_width), height / (above + below - LABEL_ROOM))
        return max(MIN_FIT_ZOOM, zoom)

    def _focus(self, ring: Ring) -> QPointF:
        """Camera centre that centres ``ring`` vertically, front labels included."""
        zoom = self.fit_zoom(ring)
        _, above, below = self._extent(ring, zoom)
        return QPointF(ring.center.x(), ring.center.y() + (below - above) / 2 / zoom)

    def to_screen(self, world: QPointF) -> QPointF:
        cam = self.camera
        return QPointF(
            (world.x() - cam.center.x()) * cam.zoom + self.width() / 2,
            (world.y() - cam.center.y()) * cam.zoom + self._anchor_y(),
        )

    def _aim_camera(self) -> None:
        """Camera target: the current ring, fitted (not during a flight). Hover is the lens's job."""
        ring = self.current
        if ring is None or self._flight is not None:
            return
        self.camera.target_center = self._focus(ring)
        self.camera.target_zoom = self.fit_zoom(ring)

    # -- geometry -------------------------------------------------------------------

    def item_geometry(self, ring: Ring) -> list[ItemGeometry]:
        """Where each visible item of ``ring`` is drawn, lens and depth of field applied."""
        zoom = self.camera.zoom
        is_current = ring is self.current
        mouse = self._mouse if (is_current and self._lens > 0.0) else None
        items = []
        for i, entry in enumerate(ring.entries):
            visibility = ring.visibility(i)
            if visibility <= 0.0:
                continue
            depth = (math.sin(ring.angle_of(i)) + 1) / 2
            size = (
                ICON_FRONT * ring.scale * zoom * (ICON_BACK_FACTOR + (1 - ICON_BACK_FACTOR) * depth)
            )
            pos = self.to_screen(ring.world_pos(i))
            blur = BLUR_BACK * (1 - depth) if is_current else BLUR_PARENT
            magnified = 0.0
            if mouse is not None:
                dx, dy = pos.x() - mouse.x(), pos.y() - mouse.y()
                distance = math.hypot(dx, dy)
                magnified = _smoothstep(1 - distance / LENS_RADIUS) * self._lens
                pos = QPointF(
                    pos.x() + dx * magnified * LENS_PUSH, pos.y() + dy * magnified * LENS_PUSH
                )
                size *= 1 + LENS_GROW * magnified
                soft = _smoothstep((distance - BLUR_START) / BLUR_RUNOFF) * self._lens * BLUR_CURSOR
                blur = max(blur * (1 - magnified), soft)
            item = ItemGeometry(entry, i, pos, size, depth, visibility, blur, magnified)
            items.append(item)
        if is_current:
            front = ring.front_index()
            for item in items:
                if item.index == self._hover_index:
                    item.blur = 0.0  # what you point at is always in focus
                elif item.index == front:
                    item.blur = min(item.blur, BLUR_FRONT)
        return items

    def item_at(self, pos: QPointF) -> tuple[Ring, ItemGeometry] | None:
        """Front-most item under ``pos`` on the current ring."""
        ring = self.current
        if ring is None:
            return None
        for item in sorted(self.item_geometry(ring), key=lambda g: -_z(g)):
            if item.visibility < 0.5:
                continue
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
        # From where a snap under way will land, so quick wheel notches and key repeats add up.
        front = self._snap_front(ring)
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

    def mouse_roll_speed(self) -> float:
        """Slots per second the cursor asks for: 0 in the middle, rising towards either side.

        Positive rolls the entries on the right towards the front.
        """
        if not self.mouse_roll or self._mouse is None or self._dragging or self.width() <= 0:
            return 0.0
        if self._flight is not None:
            return 0.0
        half = self.width() / 2
        nx = max(-1.0, min(1.0, (self._mouse.x() - half) / half))
        if abs(nx) <= ROLL_ZONE:
            return 0.0
        strength = ((abs(nx) - ROLL_ZONE) / (1 - ROLL_ZONE)) ** 2
        if self._hover_index is not None:
            strength *= ROLL_OVER_ITEM
        return math.copysign(strength * ROLL_MAX, nx)

    def _update_hover(self) -> None:
        """Re-pick the item under a still cursor (the ring or the lens moved it)."""
        if self._mouse is None or self._dragging:
            return
        hit = self.item_at(self._mouse)
        self._hover_index = hit[1].index if hit else None

    def set_mouse_roll(self, on: bool) -> None:
        self.mouse_roll = on
        self._start()

    def _tick(self) -> None:
        dt = TICK_MS / 1000.0
        moving = False
        ring = self.current
        cam = self.camera
        # The lens fades in while the cursor is over the widget (not while dragging or flying).
        lens_target = 1.0 if (self._mouse is not None and not self._dragging) else 0.0
        if self._flight is not None:
            lens_target = 0.0
        if abs(self._lens - lens_target) > 0.01:
            self._lens += (lens_target - self._lens) * LENS_EASE
            moving = True
        else:
            self._lens = lens_target
        # Mouse roll: the cursor held towards a side rolls the ring; a snap follows when it stops.
        roll = self.mouse_roll_speed() if ring is not None and ring.entries else 0.0
        if roll:
            ring.rotation += roll * ring.step * dt
            ring.velocity = 0.0
            self._snap_target = None
            self._update_hover()
            moving = True
        # Rolling: momentum after a drag, then a snap so an item sits exactly at the front.
        elif ring is not None:
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
            start, end, zoom_from, zoom_to, t = self._flight
            t = min(1.0, t + dt / FLIGHT_SECONDS)
            e = _smoothstep(t)
            cam.center = QPointF(
                start.x() + (end.x() - start.x()) * e, start.y() + (end.y() - start.y()) * e
            )
            base = zoom_from + (zoom_to - zoom_from) * e
            cam.zoom = base * (1.0 - FLIGHT_DIP * math.sin(math.pi * t))
            self._flight = (start, end, zoom_from, zoom_to, t) if t < 1.0 else None
            if self._flight is None:
                cam.center, cam.zoom = QPointF(end), zoom_to
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
        if moving and self._mouse is not None:
            self._update_hover()
        if not moving:
            self._timer.stop()
        self.update()

    def settle(self) -> None:
        """Finish every animation now (tests, and reveal)."""
        for r in self.rings:
            r.scale, r.alpha, r.velocity = r.target_scale, r.target_alpha, 0.0
        self.rings = [r for r in self.rings if not r.dying]
        ring = self.current
        if ring is not None and ring.entries:
            if self._snap_target is None:  # what the next tick would do: snap to the nearest
                self._snap_target = ring.rotation_to_front(ring.front_index() or 0)
            ring.rotation = self._snap_target
            self._snap_target = None
        self._flight = None
        self._lens = 1.0 if (self._mouse is not None and not self._dragging) else 0.0
        self._aim_camera()
        self.camera.center = QPointF(self.camera.target_center)
        self.camera.zoom = self.camera.target_zoom
        self._timer.stop()
        self.update()

    # -- events -----------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        """Refit to the new size; a flight in progress retargets itself."""
        super().resizeEvent(event)
        ring = self.current
        if ring is None:
            return
        if self._flight is not None:
            start, _, zoom_from, _, t = self._flight
            self._flight = (start, self._focus(ring), zoom_from, self.fit_zoom(ring), t)
            return
        self._aim_camera()
        self.camera.center = QPointF(self.camera.target_center)
        self.camera.zoom = self.camera.target_zoom

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging and (pos - self._press_pos).manhattanLength() > DRAG_THRESHOLD:
                self._dragging = True
                self._hover_index = None
                self._aim_camera()
                self._start()  # the lens lets go while dragging
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
        self._mouse = QPointF(pos)
        hit = self.item_at(pos)
        self._hover_index = hit[1].index if hit else None
        self._start()  # the lens follows the cursor; the edges may roll the ring
        self.update()
        if hit:
            entry = hit[1].entry
            tip = entry.path if not entry.is_parent else "Go to the parent folder"
            if not entry.is_dir and hit[0].front_index() == hit[1].index:
                tip += "\n(click to put this path in the message; double-click to open)"
            self.setToolTip(tip)
        else:
            self.setToolTip("")

    def leaveEvent(self, event) -> None:
        self._mouse = None
        self._hover_index = None
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
        elif self._is_typing(event):
            self.type_ahead(event.text())
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _is_typing(event: QKeyEvent) -> bool:
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        text = event.text()
        return bool(text.strip()) and text.isprintable() and not event.modifiers() & modifiers

    def type_ahead(self, text: str) -> None:
        """Bring the first entry starting with what was just typed to the front.

        Repeating one letter ("sss") steps through the entries starting with it.
        """
        now = time.monotonic()
        if now - self._typed_at > TYPE_AHEAD_SECONDS:
            self._typed = ""
        self._typed += text.lower()
        self._typed_at = now
        ring = self.current
        if ring is None:
            return

        def matches(prefix: str) -> list[int]:
            return [
                i
                for i, e in enumerate(ring.entries)
                if not e.is_parent and e.name.lower().startswith(prefix)
            ]

        found = matches(self._typed)
        if found:
            self.bring_to_front(ring, found[0])
            return
        letter = self._typed[-1]
        if self._typed == letter * len(self._typed) and (found := matches(letter)):
            front = self._snap_front(ring)
            later = [i for i in found if i > front]
            self.bring_to_front(ring, later[0] if later else found[0])

    def _snap_front(self, ring: Ring) -> int:
        """The entry at the front once the current snap (if any) finishes."""
        if self._snap_target is None or not ring.entries:
            return ring.front_index() or 0
        position = (math.pi / 2 - self._snap_target) / ring.step
        return round(position) % len(ring.entries)

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
        reveal_target = (
            hit[1].entry.path if (hit is not None and not hit[1].entry.is_parent) else current
        )
        menu.addAction(
            f"Open in {file_manager_name()}",
            lambda: self.reveal_requested.emit(os.path.abspath(reveal_target)),
        )
        menu.addSeparator()
        hidden = menu.addAction("Show hidden files")
        hidden.setCheckable(True)
        hidden.setChecked(self.show_hidden)
        hidden.toggled.connect(self.set_show_hidden)
        roll = menu.addAction("Roll with the mouse at the sides")
        roll.setCheckable(True)
        roll.setChecked(self.mouse_roll)
        roll.toggled.connect(self.set_mouse_roll)
        roll.toggled.connect(self.mouse_roll_changed.emit)
        menu.addAction("Refresh", self.refresh)
        menu.exec(event.globalPos())

    # -- labels -------------------------------------------------------------------------

    def _label_font(self, item: ItemGeometry, is_front: bool) -> QFont:
        font = QFont()
        font.setPointSizeF(max(6.5, self.font().pointSizeF() - 2 + 3 * item.depth))
        font.setBold(is_front)
        return font

    def _label_rect(self, item: ItemGeometry, is_front: bool) -> QRectF:
        width = 90 + 80 * item.depth if is_front else 44 + 36 * item.depth
        top = item.pos.y() + (item.size * 0.78 + 7 if is_front else item.size / 2 + 1)
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
        metrics = QFontMetricsF(font)
        crumb = _shorten_path(self.current_path(), Path.home())
        counter_width = metrics.horizontalAdvance(_counter_text(self.current) + "   ")
        painter.drawText(
            QRectF(8, 4, self.width() - 16, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(
                crumb, Qt.TextElideMode.ElideMiddle, self.width() - 16 - counter_width
            ),
        )
        current = self.current
        counter = _counter_text(current)
        if counter:
            painter.drawText(
                QRectF(8, 4, self.width() - 16, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                counter,
            )
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
        items = [g for g in sorted(self.item_geometry(ring), key=_z) if widget_rect.contains(g.pos)]
        front = ring.front_index()
        labelled: set[int] = set()
        taken: list[QRectF] = []
        if ring.scale > 0.7:
            # Front to back: a label may not cover another label or an icon nearer the viewer.
            for item in reversed(items):
                if item.visibility < 0.5:
                    continue
                rect = self._label_rect(item, is_front=item.index == front)
                if not any(rect.intersects(other) for other in taken):
                    taken.append(rect)
                    labelled.add(item.index)
                half = item.size * 0.34
                taken.append(QRectF(item.pos.x() - half, item.pos.y() - half, 2 * half, 2 * half))
        for item in items:
            self._paint_item(
                painter, item, ring, is_front=item.index == front, label=item.index in labelled
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
        dim = (0.35 + 0.65 * item.depth) * item.visibility
        dim = min(1.0, dim + 0.35 * item.magnified)  # the lens also brightens
        painter.setOpacity(ring.alpha * dim)
        hovered = ring is self.current and item.index == self._hover_index
        kind = "parent" if item.entry.is_parent else "dir" if item.entry.is_dir else "file"
        highlight = is_front or hovered
        level = round(item.blur * (BLUR_LEVELS - 1))
        if level == 0:
            _draw_icon(painter, t, kind, item.pos.x(), item.pos.y(), size, highlight)
        else:
            pixmap = self._icon_pixmap(kind, highlight, size, level)
            ratio = pixmap.devicePixelRatio()
            w, h = pixmap.width() / ratio, pixmap.height() / ratio
            scale = size / _bucket(size)
            target = QRectF(
                item.pos.x() - w * scale / 2, item.pos.y() - h * scale / 2, w * scale, h * scale
            )
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        if highlight and ring.scale > 0.7:
            painter.setPen(QPen(QColor(t.accent_hover), 2.0 if is_front else 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(item.pos, size * 0.78, size * 0.78)
        if label:
            painter.setOpacity(ring.alpha * dim * (1 - 0.6 * item.blur))
            font = self._label_font(item, is_front)
            painter.setFont(font)
            painter.setPen(QColor(t.text if (is_front or hovered) else t.text_muted))
            rect = self._label_rect(item, is_front)
            text = QFontMetricsF(font).elidedText(
                item.entry.name + ("/" if item.entry.is_dir else ""),
                Qt.TextElideMode.ElideMiddle,
                rect.width(),
            )
            painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        painter.setOpacity(ring.alpha)

    def _icon_pixmap(self, kind: str, highlight: bool, size: float, level: int) -> QPixmap:
        """An icon rendered at (about) ``size`` px and blurred to ``level``, from the cache."""
        ratio = self.devicePixelRatioF() or 1.0
        bucket = _bucket(size)
        key = (kind, highlight, bucket, level, ratio)
        pixmap = self._icons.get(key)
        if pixmap is not None:
            self._icons.move_to_end(key)
            return pixmap
        radius = BLUR_MAX_PX * (bucket / ICON_FRONT) * level / (BLUR_LEVELS - 1)
        side = bucket * 1.2 + 4 * radius + 4  # room for the blur to spread
        image = QPixmap(max(1, round(side * ratio)), max(1, round(side * ratio)))
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_icon(p, self.theme, kind, side / 2, side / 2, bucket, highlight)
        p.end()
        pixmap = _blurred(image, radius * ratio)
        pixmap.setDevicePixelRatio(ratio)
        self._icons[key] = pixmap
        while len(self._icons) > ICON_CACHE_SIZE:
            self._icons.popitem(last=False)
        return pixmap


def _z(item: ItemGeometry) -> float:
    """Drawing order: nearer items on top, and whatever the lens magnifies above them all."""
    return item.depth + 2 * item.magnified


def _bucket(size: float) -> int:
    """Icon sizes are cached in 3 px steps."""
    return max(6, int(round(size / 3)) * 3)


def _blurred(pixmap: QPixmap, radius: float) -> QPixmap:
    """A cheap, good-looking blur: shrink with smoothing and grow back, twice."""
    if radius < 0.5:
        return pixmap
    w, h = pixmap.width(), pixmap.height()
    factor = 1 + radius / 1.6
    small_w, small_h = max(1, round(w / factor)), max(1, round(h / factor))
    smooth = Qt.TransformationMode.SmoothTransformation
    keep = Qt.AspectRatioMode.IgnoreAspectRatio
    result = pixmap
    for _ in range(2):
        result = result.scaled(small_w, small_h, keep, smooth).scaled(w, h, keep, smooth)
    return result


def _draw_icon(
    painter: QPainter, t: ThemeConfig, kind: str, x: float, y: float, size: float, highlight: bool
) -> None:
    """The vector icon for ``kind`` (parent, dir, file) centred on (x, y)."""
    if kind == "parent":
        color = QColor(t.accent_hover if highlight else t.text_muted)
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
    elif kind == "dir":
        color = QColor(t.accent_hover if highlight else t.accent)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(x - size / 2, y - size * 0.32, size, size * 0.72), size * 0.1, size * 0.1
        )
        path.addRoundedRect(
            QRectF(x - size / 2, y - size * 0.44, size * 0.45, size * 0.2),
            size * 0.06,
            size * 0.06,
        )
        painter.fillPath(path, color)
    else:
        color = QColor(t.text if highlight else t.text_muted)
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


def _counter_text(ring: Ring | None) -> str:
    """ "12 / 61" when the ring scrolls, plus the entries past MAX_ENTRIES that were left out."""
    if ring is None or not ring.scrolls:
        return ""
    real = len(ring.entries) - (1 if ring.entries[0].is_parent else 0)
    position = (ring.front_index() or 0) + (0 if ring.entries[0].is_parent else 1)
    text = f"{position} / {real}" if position else f".. / {real}"
    if ring.hidden_count:
        text += f"  +{ring.hidden_count} not shown"
    return text


def file_manager_name() -> str:
    if sys.platform == "win32":
        return "File Explorer"
    if sys.platform == "darwin":
        return "Finder"
    return "file manager"


def _shorten_path(path: str, home: Path) -> str:
    try:
        return "~/" + str(Path(path).relative_to(home)) if Path(path) != home else "~"
    except ValueError:
        return path
