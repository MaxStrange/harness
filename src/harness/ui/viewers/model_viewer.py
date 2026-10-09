"""Embedded 3D model viewer: GLB, glTF, STL and OBJ, for a quick look (viewing only).

three.js runs in QtWebEngine (model_web/). Left drag rotates, Shift+left drag (or right
drag) pans, the wheel zooms; Reset view goes back to the fitted view.
"""

from __future__ import annotations

import base64
import importlib.resources
import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from harness.config import ThemeConfig

log = logging.getLogger(__name__)

MODEL_SUFFIXES = (".glb", ".gltf", ".stl", ".obj")
MAX_MODEL_BYTES = 200 * 2**20  # what is sent to the page at once


class ViewerBridge(QObject):
    """Exposed to the page as ``viewer``."""

    load = Signal(str, str, str)  # name, extension, base64 bytes
    reset = Signal()
    became_ready = Signal()
    finished = Signal(bool, str)  # ok, size and triangles or the error

    def __init__(self) -> None:
        super().__init__()
        self.is_ready = False

    @Slot()
    def ready(self) -> None:
        self.is_ready = True
        self.became_ready.emit()

    @Slot(str)
    def loaded(self, info: str) -> None:
        self.finished.emit(True, info)

    @Slot(str)
    def failed(self, error: str) -> None:
        self.finished.emit(False, error)


class ModelViewer(QWidget):
    def __init__(self, theme: ThemeConfig) -> None:
        super().__init__()
        self.path: str | None = None
        self.info = ""
        self._pending: tuple[str, str, str] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        self.title = QLabel("No model")
        self.title.setObjectName("panelTitle")
        header.addWidget(self.title, 1)
        self.reset_button = QPushButton("Reset view")
        self.reset_button.setToolTip(
            "Back to the fitted view. Left drag rotates, Shift+left drag pans, the wheel zooms."
        )
        header.addWidget(self.reset_button)
        layout.addLayout(header)
        self.status = QLabel("Left drag rotates, Shift+left drag pans, the wheel zooms")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)  # a long line must not widen the side panel
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)

        self.bridge = ViewerBridge()
        self.bridge.became_ready.connect(self._send_pending)
        self.bridge.finished.connect(self._finished)
        self.reset_button.clicked.connect(self.bridge.reset.emit)
        self.view = QWebEngineView()
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("viewer", self.bridge)
        self.view.page().setWebChannel(self.channel)
        index = importlib.resources.files("harness.ui.viewers").joinpath("model_web", "index.html")
        url = QUrl.fromLocalFile(str(index))
        query = QUrlQuery()
        query.addQueryItem(
            "theme",
            json.dumps({"bg": theme.background, "text": theme.text, "muted": theme.text_muted}),
        )
        url.setQuery(query)
        self.view.setUrl(url)
        layout.addWidget(self.view, 1)

    def show_model(self, path: str) -> str | None:
        """Load ``path`` into the view. Returns an error, or None once it is on its way."""
        file = Path(path)
        if file.suffix.lower() not in MODEL_SUFFIXES:
            return f"{file.name}: not a model the viewer reads ({', '.join(MODEL_SUFFIXES)})"
        try:
            size = file.stat().st_size
            if size > MAX_MODEL_BYTES:
                return f"{file.name} is {size // 2**20} MB; the viewer takes up to 200 MB"
            data = file.read_bytes()
        except OSError as exc:
            return f"cannot read {file}: {exc.strerror or exc}"
        self.path, self.info = str(file), ""
        self.title.setText(file.name)
        self.status.setText("Loading...")
        self._pending = (
            file.name,
            file.suffix.lstrip(".").lower(),
            base64.b64encode(data).decode(),
        )
        if self.bridge.is_ready:
            self._send_pending()
        return None

    def _send_pending(self) -> None:
        if self._pending is not None:
            self.bridge.load.emit(*self._pending)
            self._pending = None

    def _finished(self, ok: bool, text: str) -> None:
        self.info = text if ok else ""
        self.status.setText(
            text + "  -  left drag rotates, Shift+left drag pans"
            if ok
            else f"Cannot show it: {text}"
        )
        if not ok:
            log.warning("3D viewer could not show %s: %s", self.path, text)
