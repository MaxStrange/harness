"""The orbiting file explorer (UI3).

Each entry of a directory is an icon on a ring seen almost edge-on: a wide, flat
ellipse where the item at the front is largest and brightest and the ones at
the back are small and dim. Moving the mouse left or right of centre rolls the
ring; the wheel and the arrow keys step it. Clicking a directory expands it:
its ring shrinks and slides up into a row of parent rings while the children
grow into the middle. Clicking a parent ring (or Backspace) goes back up.

Inspired by the Screenvader site. The widget exposes the same interface as the
tree stub (``set_root``, ``reveal``, ``open_requested``, ``cwd_requested``) so
the main window does not care which one is in use.
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

MAX_ENTRIES = 240  # more than this on one ring becomes a blur; the rest is counted
TICK_MS = 16
MAX_ROLL_SPEED = 2.2  # radians per second at the widget's edge
DEAD_ZONE = 0.12  # fraction of half-width around the centre that does not roll
EASE = 0.18  # per tick, for ring transforms
RING_RATIO = 0.26  # ellipse height / width: "almost in plane"
ICON_FRONT = 44.0  # px, front icon size on a full-size ring
ICON_BACK_FACTOR = 0.42  # back icons are this fraction of the front size
STACK_SCALE = 0.34  # parent rings shrink to this
STACK_ROW_Y = 0.20  # parent rings sit at this fraction of the height
MAIN_ROW_Y = 0.62  # the current ring's centre


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    is_parent: bool = False  # the ".." entry: goes up instead of expanding


PARENT_NAME = ".."
WHEEL_STEP_DEGREES = 15.0  # wheel travel per item step: one notch
PIXEL_STEP = 40.0  # touchpad pixel travel per item step


@dataclass
class Ring:
    path: str
    entries: list[Entry]
    hidden_count: int = 0
    rotation: float = 0.0  # radians; item i sits at rotation + i * step
    velocity: float = 0.0  # radians per second
    scale: float = 0.2
    alpha: float = 0.0
    center: QPointF = field(default_factory=QPointF)
    target_scale: float = 1.0
    target_alpha: float = 1.0
    target_center: QPointF = field(default_factory=QPointF)
    dying: bool = False

    @property
    def step(self) -> float:
        return 2 * math.pi / max(1, len(self.entries))

    def angle_of(self, index: int) -> float:
        return self.rotation + index * self.step

    def front_index(self) -> int | None:
        """The entry closest to the front (angle pi/2)."""
        if not self.entries:
            return None
        best, best_depth = 0, -2.0
        for i in range(len(self.entries)):
            depth = math.sin(self.angle_of(i))
            if depth > best_depth:
                best, best_depth = i, depth
        return best

    def rotation_to_front(self, index: int) -> float:
        """The rotation that puts ``index`` exactly at the front, nearest to the current one."""
        target = math.pi / 2 - index * self.step
        turns = round((self.rotation - target) / (2 * math.pi))
        return target + turns * 2 * math.pi


@dataclass
class ItemGeometry:
    entry: Entry
    index: int
    pos: QPointF
    size: float
    depth: float  # 0 = back, 1 = front


class OrbitExplorer(QWidget):
    open_requested = Signal(str)
    cwd_requested = Signal(str)
    directory_changed = Signal(str)

    def __init__(self, root: str, theme: ThemeConfig, show_hidden: bool = False) -> None:
        super().__init__()
        self.theme = theme
        self.show_hidden = show_hidden
        self.rings: list[Ring] = []
        self._root = root
        self._hover_dx = 0.0
        self._hovering = False
        self._wheel_accum = 0.0
        self._snap_target: float | None = None
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
        self._push_ring(self._root)
        self._layout_rings(animate=False)
        self.update()

    def reveal(self, path: str) -> None:
        """Expand down to ``path`` (a directory shows its contents; a file is brought to the front)."""
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
        self._layout_rings(animate=False)
        self.update()

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

    def _push_ring(self, path: str) -> Ring:
        entries, hidden = self._read_dir(path)
        ring = Ring(path=path, entries=entries, hidden_count=hidden)
        first = 1 if ring.entries and ring.entries[0].is_parent and len(ring.entries) > 1 else 0
        ring.rotation = math.pi / 2 - first * ring.step  # first real entry at the front
        ring.center = QPointF(self.width() / 2, self.height() * MAIN_ROW_Y)
        self.rings.append(ring)
        self.directory_changed.emit(path)
        return ring

    def expand(self, entry: Entry) -> None:
        if not entry.is_dir:
            return
        if entry.is_parent:
            self.go_up()
            return
        self._push_ring(entry.path)
        self._layout_rings(animate=True)
        self._start()

    def collapse_to(self, depth: int) -> None:
        """Make ring ``depth`` current; deeper rings fade out."""
        live = [r for r in self.rings if not r.dying]
        for ring in live[depth + 1 :]:
            ring.dying = True
            ring.target_alpha = 0.0
            ring.target_scale = 0.2
        if depth < len(live):
            self.directory_changed.emit(live[depth].path)
        self._layout_rings(animate=True)
        self._start()

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

    def _layout_rings(self, animate: bool) -> None:
        live = [r for r in self.rings if not r.dying]
        w, h = self.width(), self.height()
        parents = live[:-1]
        shown = parents[-3:]  # the row holds the last three parents
        slot_w = w / (len(shown) + 1) if shown else w
        for i, ring in enumerate(parents):
            ring.target_scale = STACK_SCALE if ring in shown else 0.0
            ring.target_alpha = 1.0 if ring in shown else 0.0
            if ring in shown:
                ring.target_center = QPointF(slot_w * (shown.index(ring) + 1), h * STACK_ROW_Y)
            else:
                ring.target_center = QPointF(0, h * STACK_ROW_Y)
        if live:
            top = live[-1]
            top.target_scale = 1.0
            top.target_alpha = 1.0
            top.target_center = QPointF(w / 2, h * MAIN_ROW_Y)
        if not animate:
            for ring in live:
                ring.scale, ring.alpha = ring.target_scale, ring.target_alpha
                ring.center = QPointF(ring.target_center)

    # -- geometry -------------------------------------------------------------------

    def _radii(self, ring: Ring) -> tuple[float, float]:
        rx = max(40.0, (self.width() - ICON_FRONT - 28) / 2) * ring.scale
        return rx, rx * RING_RATIO

    def item_geometry(self, ring: Ring) -> list[ItemGeometry]:
        rx, ry = self._radii(ring)
        items = []
        for i, entry in enumerate(ring.entries):
            angle = ring.angle_of(i)
            depth = (math.sin(angle) + 1) / 2
            size = ICON_FRONT * ring.scale * (ICON_BACK_FACTOR + (1 - ICON_BACK_FACTOR) * depth)
            pos = QPointF(
                ring.center.x() + rx * math.cos(angle), ring.center.y() + ry * math.sin(angle)
            )
            items.append(ItemGeometry(entry, i, pos, size, depth))
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
            rx, ry = self._radii(ring)
            dx, dy = pos.x() - ring.center.x(), pos.y() - ring.center.y()
            if (dx / (rx + 20)) ** 2 + (dy / (ry + 30)) ** 2 <= 1:
                return ring
        return None

    # -- motion ---------------------------------------------------------------------

    def roll(self, delta: float) -> None:
        """Rotate the current ring by ``delta`` radians immediately (tests, keyboard)."""
        ring = self.current
        if ring is not None:
            ring.rotation += delta
            ring.velocity = 0.0
            self._snap_target = None
            self.update()

    def step(self, direction: int) -> None:
        ring = self.current
        if ring is None or not ring.entries:
            return
        front = ring.front_index()
        assert front is not None
        index = (front + direction) % len(ring.entries)
        self.bring_to_front(ring, index)

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
        if ring is not None:
            if self._hovering and abs(self._hover_dx) > DEAD_ZONE:
                strength = (abs(self._hover_dx) - DEAD_ZONE) / (1 - DEAD_ZONE)
                ring.velocity = -math.copysign(strength * strength * MAX_ROLL_SPEED, self._hover_dx)
                self._snap_target = None
            else:
                ring.velocity *= 0.82
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
        for r in self.rings:
            if (
                abs(r.scale - r.target_scale) > 0.003
                or abs(r.alpha - r.target_alpha) > 0.01
                or (r.center - r.target_center).manhattanLength() > 0.5
            ):
                r.scale += (r.target_scale - r.scale) * EASE
                r.alpha += (r.target_alpha - r.alpha) * EASE
                r.center = QPointF(
                    r.center.x() + (r.target_center.x() - r.center.x()) * EASE,
                    r.center.y() + (r.target_center.y() - r.center.y()) * EASE,
                )
                moving = True
            elif r.dying:
                r.alpha = 0.0
        if any(r.dying and r.alpha < 0.02 for r in self.rings):
            self.rings = [r for r in self.rings if not (r.dying and r.alpha < 0.02)]
        if not moving:
            self._timer.stop()
        self.update()

    def settle(self) -> None:
        """Finish every animation now (tests)."""
        for r in self.rings:
            r.scale, r.alpha, r.center = r.target_scale, r.target_alpha, QPointF(r.target_center)
            r.velocity = 0.0
        self.rings = [r for r in self.rings if not r.dying]
        ring = self.current
        if ring is not None and self._snap_target is not None:
            ring.rotation = self._snap_target
            self._snap_target = None
        self._timer.stop()
        self.update()

    # -- events -----------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_rings(animate=False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        half = self.width() / 2
        self._hover_dx = max(-1.0, min(1.0, (event.position().x() - half) / half))
        self._hovering = True
        hit = self.item_at(event.position())
        self.setToolTip(hit[1].entry.path if hit else "")
        self._start()

    def leaveEvent(self, event) -> None:
        self._hovering = False
        self._start()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Mouse wheel (either axis) and touchpad scroll gestures both roll the ring.

        A horizontal two-finger swipe arrives as a wheel event with an x delta; touchpads
        also report pixel deltas, which are accumulated so small gestures still add up.
        """
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
        if event.button() != Qt.MouseButton.LeftButton:
            return
        parent = self.parent_ring_at(event.position())
        if parent is not None:
            live = [r for r in self.rings if not r.dying]
            self.collapse_to(live.index(parent))
            return
        hit = self.item_at(event.position())
        if hit is None:
            return
        ring, item = hit
        if item.entry.is_dir:
            self.expand(item.entry)
        else:
            self.bring_to_front(ring, item.index)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        hit = self.item_at(event.position())
        if hit is not None and not hit[1].entry.is_dir:
            self.open_requested.emit(hit[1].entry.path)

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
                    self.open_requested.emit(entry.path)
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
                menu.addAction("Open in editor", lambda: self.open_requested.emit(entry.path))
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
        # Breadcrumb.
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
        for ring in self.rings:
            if ring.alpha <= 0.01 or ring.scale <= 0.01:
                continue
            self._paint_ring(painter, ring, ring is self.current)
        painter.end()

    def _paint_ring(self, painter: QPainter, ring: Ring, is_current: bool) -> None:
        t = self.theme
        rx, ry = self._radii(ring)
        painter.setOpacity(ring.alpha)
        # The orbit itself.
        pen = QPen(QColor(t.border), 1.0)
        pen.setStyle(Qt.PenStyle.DashLine if not is_current else Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring.center, rx, ry)
        if not ring.entries:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(
                QRectF(ring.center.x() - rx, ring.center.y() - 10, 2 * rx, 20),
                Qt.AlignmentFlag.AlignCenter,
                "(empty)" if ring.scale > 0.6 else "",
            )
        items = sorted(self.item_geometry(ring), key=lambda g: g.depth)
        front = ring.front_index()
        # Labels go front to back and a label that would overlap one already placed is skipped,
        # so a dense ring stays readable instead of turning into a pile of names.
        labelled: set[int] = set()
        taken: list[QRectF] = []
        if ring.scale > 0.6:
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
                QRectF(ring.center.x() - rx, ring.center.y() + ry + 34, 2 * rx, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"+{ring.hidden_count} more (use find_files or the terminal)",
            )
        if not is_current and ring.scale > 0.05:
            painter.setPen(QColor(t.text_muted))
            font = QFont()
            font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
            painter.setFont(font)
            name = os.path.basename(ring.path.rstrip("/\\")) or ring.path
            painter.drawText(
                QRectF(ring.center.x() - rx - 20, ring.center.y() + ry + 12, 2 * rx + 40, 16),
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
        if item.entry.is_parent:
            color = QColor(t.accent_hover if is_front else t.text_muted)
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
            color = QColor(t.accent_hover if is_front else t.accent)
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
            color = QColor(t.text if is_front else t.text_muted)
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
        if is_front and ring.scale > 0.6:
            painter.setPen(QPen(QColor(t.accent_hover), 2.0))
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
