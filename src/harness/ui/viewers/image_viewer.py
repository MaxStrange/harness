"""Embedded image viewer (S10): tabs of images, fit-to-window with zoom.

Every image shown gets a tab (showing one that is already open selects its tab
and reloads it), so a run of generated images can be compared by flipping
between tabs. The zoom and scroll position carry over when switching tabs, so
the same detail of each image lines up. Left/Right (or Ctrl+Tab and
Ctrl+Shift+Tab) flip between tabs; Ctrl+W or a middle click closes one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QPixmap, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

MAX_TABS = 40  # the oldest tab closes beyond this


@dataclass
class ImageTab:
    path: str
    pixmap: QPixmap | None  # None when the file could not be read


class ImageViewer(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tab_bar = QTabBar()
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.currentChanged.connect(self._on_current_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._tab_menu)
        self.tab_bar.installEventFilter(self)
        self.tab_bar.hide()  # shown once there is an image
        layout.addWidget(self.tab_bar)
        header = QHBoxLayout()
        self.title = QLabel("No image")
        self.title.setObjectName("panelTitle")
        header.addWidget(self.title, 1)
        for text, handler in (
            ("Fit", self.fit),
            ("100%", self.actual_size),
            ("+", lambda: self.zoom(1.25)),
            ("-", lambda: self.zoom(0.8)),
        ):
            button = QPushButton(text)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep the arrows for flipping
            button.clicked.connect(handler)
            header.addWidget(button)
        layout.addLayout(header)
        self.area = QScrollArea()
        self.area.setWidgetResizable(False)
        self.area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setWidget(self.label)
        layout.addWidget(self.area, 1)
        self.tabs: list[ImageTab] = []
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._fit = True
        for keys, handler in (
            ("Ctrl+Tab", lambda: self.step(1)),
            ("Ctrl+Shift+Tab", lambda: self.step(-1)),
            ("Ctrl+W", lambda: self.close_tab(self.tab_bar.currentIndex())),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

    # -- tabs ---------------------------------------------------------------------

    def show_image(self, path: str) -> bool:
        """Show ``path`` in its own tab (reusing and reloading its tab if already open)."""
        path = os.path.abspath(path)
        pixmap = QPixmap(path)
        tab = ImageTab(path, None if pixmap.isNull() else pixmap)
        index = self._index_of(path)
        if index is None:
            self.tabs.append(tab)
            index = self.tab_bar.addTab(Path(path).name)
            while len(self.tabs) > MAX_TABS:
                self.close_tab(0)
                index -= 1
        else:
            self.tabs[index] = tab
        self.tab_bar.setTabToolTip(index, path)
        self.tab_bar.show()
        self._fit = True  # a new image starts fitted; flipping between tabs keeps the zoom
        if self.tab_bar.currentIndex() == index:
            self._display(index)
        else:
            self.tab_bar.setCurrentIndex(index)
        return tab.pixmap is not None

    def close_tab(self, index: int) -> None:
        if 0 <= index < len(self.tabs):
            del self.tabs[index]
            self.tab_bar.removeTab(index)  # emits currentChanged for the new current tab
        if not self.tabs:
            self.tab_bar.hide()
            self._show_nothing()

    def close_other_tabs(self, keep: int) -> None:
        for index in reversed(range(len(self.tabs))):
            if index != keep:
                self.close_tab(index)

    def step(self, direction: int) -> None:
        if self.tabs:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + direction) % len(self.tabs))

    def current_path(self) -> str | None:
        index = self.tab_bar.currentIndex()
        return self.tabs[index].path if 0 <= index < len(self.tabs) else None

    def _index_of(self, path: str) -> int | None:
        key = os.path.normcase(path)
        return next((i for i, t in enumerate(self.tabs) if os.path.normcase(t.path) == key), None)

    def _on_current_changed(self, index: int) -> None:
        if 0 <= index < len(self.tabs):
            self._display(index)

    def _on_tab_moved(self, source: int, target: int) -> None:
        self.tabs.insert(target, self.tabs.pop(source))

    def _tab_menu(self, pos) -> None:
        index = self.tab_bar.tabAt(pos)
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("Close", lambda: self.close_tab(index))
        menu.addAction("Close others", lambda: self.close_other_tabs(index))
        menu.addAction("Close all", lambda: self.close_other_tabs(-1))
        menu.exec(self.tab_bar.mapToGlobal(pos))

    def eventFilter(self, watched, event) -> bool:
        # Middle click closes a tab, as in browsers.
        if (
            watched is self.tab_bar
            and event.type() == event.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.MiddleButton
        ):
            index = self.tab_bar.tabAt(event.position().toPoint())
            if index >= 0:
                self.close_tab(index)
                return True
        return super().eventFilter(watched, event)

    # -- display ------------------------------------------------------------------

    def _display(self, index: int) -> None:
        tab = self.tabs[index]
        count = f"  [{index + 1}/{len(self.tabs)}]" if len(self.tabs) > 1 else ""
        if tab.pixmap is None:
            self.title.setText(f"Cannot display {Path(tab.path).name}{count}")
            self._pixmap = None
            self.label.clear()
            return
        # Keep the scroll position so the same region of each image lines up.
        h, v = self.area.horizontalScrollBar().value(), self.area.verticalScrollBar().value()
        self._pixmap = pixmap = tab.pixmap
        self.title.setText(f"{Path(tab.path).name}  ({pixmap.width()}x{pixmap.height()}){count}")
        self._apply()
        self.area.horizontalScrollBar().setValue(h)
        self.area.verticalScrollBar().setValue(v)

    def _show_nothing(self) -> None:
        self._pixmap = None
        self.label.clear()
        self.label.resize(0, 0)
        self.title.setText("No image")

    def fit(self) -> None:
        self._fit = True
        self._apply()

    def actual_size(self) -> None:
        self._fit = False
        self._scale = 1.0
        self._apply()

    def zoom(self, factor: float) -> None:
        if self._fit:
            self._scale = self._fit_scale()
            self._fit = False
        self._scale = max(0.05, min(20.0, self._scale * factor))
        self._apply()

    def _fit_scale(self) -> float:
        if self._pixmap is None or self._pixmap.width() == 0:
            return 1.0
        available = self.area.viewport().size()
        return min(
            available.width() / self._pixmap.width(),
            available.height() / self._pixmap.height(),
            1.0,
        )

    def _apply(self) -> None:
        if self._pixmap is None:
            return
        scale = self._fit_scale() if self._fit else self._scale
        size = self._pixmap.size() * scale
        self.label.setPixmap(
            self._pixmap.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        self.label.resize(self.label.pixmap().size())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit:
            self._apply()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not event.modifiers():
            self.step(1 if event.key() == Qt.Key.Key_Right else -1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 0.87)
            event.accept()
        else:
            super().wheelEvent(event)
