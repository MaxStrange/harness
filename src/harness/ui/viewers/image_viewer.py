"""Embedded image viewer (S10): fit-to-window with zoom."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget


class ImageViewer(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
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
            button.clicked.connect(handler)
            header.addWidget(button)
        layout.addLayout(header)
        self.area = QScrollArea()
        self.area.setWidgetResizable(False)
        self.area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setWidget(self.label)
        layout.addWidget(self.area, 1)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._fit = True

    def show_image(self, path: str) -> bool:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.title.setText(f"Cannot display {Path(path).name}")
            self._pixmap = None
            self.label.clear()
            return False
        self._pixmap = pixmap
        self.title.setText(f"{Path(path).name}  ({pixmap.width()}x{pixmap.height()})")
        self._fit = True
        self._apply()
        return True

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

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 0.87)
            event.accept()
        else:
            super().wheelEvent(event)
