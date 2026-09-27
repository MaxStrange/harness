"""The embedded terminal (P11): xterm.js in a Qt web view, bridged to a TerminalSession."""

from __future__ import annotations

import base64
import importlib.resources
import json
import logging

from PySide6.QtCore import QObject, Qt, QUrl, QUrlQuery, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from harness.config import TerminalConfig, ThemeConfig
from harness.terminal.session import TerminalSession

log = logging.getLogger(__name__)


class TerminalBridge(QObject):
    """Exposed to JavaScript as ``terminal``. Output arrives from the pty thread and is relayed
    through a queued signal so QWebChannel only ever sees GUI-thread emissions."""

    output = Signal(str)
    cleared = Signal()
    _relay = Signal(bytes)

    def __init__(self, session: TerminalSession) -> None:
        super().__init__()
        self.session = session
        self._ready = False
        self._relay.connect(self._on_relay, Qt.ConnectionType.QueuedConnection)
        session.subscribe(self._relay.emit)

    @Slot(bytes)
    def _on_relay(self, data: bytes) -> None:
        if self._ready:
            self.output.emit(base64.b64encode(data).decode("ascii"))

    @Slot()
    def ready(self) -> None:
        self._ready = True
        history = self.session.scrollback()
        if history:
            self.output.emit(base64.b64encode(history).decode("ascii"))

    @Slot(str)
    def input(self, b64: str) -> None:
        try:
            self.session.write(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001
            log.warning("terminal input failed: %s", exc)

    @Slot(int, int)
    def resized(self, cols: int, rows: int) -> None:
        if cols > 0 and rows > 0:
            try:
                self.session.resize(cols, rows)
            except Exception as exc:  # noqa: BLE001
                log.warning("terminal resize failed: %s", exc)

    def detach(self) -> None:
        self.session.unsubscribe(self._relay.emit)


def xterm_theme(theme: ThemeConfig) -> dict:
    return {
        "background": theme.code_background,
        "foreground": theme.text,
        "cursor": theme.accent,
        "selectionBackground": theme.border,
        "green": theme.accent,
        "brightGreen": theme.accent,
    }


class TerminalView(QWidget):
    def __init__(
        self, session: TerminalSession, config: TerminalConfig, theme: ThemeConfig
    ) -> None:
        super().__init__()
        self.session = session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView()
        self.channel = QWebChannel(self.view.page())
        self.bridge = TerminalBridge(session)
        self.channel.registerObject("terminal", self.bridge)
        self.view.page().setWebChannel(self.channel)
        index = importlib.resources.files("harness.terminal").joinpath("web").joinpath("index.html")
        url = QUrl.fromLocalFile(str(index))
        query = QUrlQuery()
        query.addQueryItem("theme", json.dumps(xterm_theme(theme)))
        query.addQueryItem("font", config.font_family)
        query.addQueryItem("size", str(config.font_size))
        query.addQueryItem("scrollback", str(config.scrollback_lines))
        url.setQuery(query)
        self.view.setUrl(url)
        layout.addWidget(self.view)

    def focus_terminal(self) -> None:
        self.view.setFocus()

    def closeEvent(self, event) -> None:
        self.bridge.detach()
        super().closeEvent(event)


class TerminalPanel(QWidget):
    """One tab per terminal session."""

    def __init__(self, config: TerminalConfig, theme: ThemeConfig) -> None:
        super().__init__()
        self.config = config
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs)
        self._views: dict[str, TerminalView] = {}

    def add_session(self, session: TerminalSession) -> TerminalView:
        existing = self._views.get(session.name)
        if existing is not None and existing.session is session:
            return existing
        if existing is not None:
            self.remove_session(session.name)
        view = TerminalView(session, self.config, self.theme)
        self._views[session.name] = view
        self.tabs.addTab(view, session.name)
        return view

    def remove_session(self, name: str) -> None:
        view = self._views.pop(name, None)
        if view is not None:
            self.tabs.removeTab(self.tabs.indexOf(view))
            view.bridge.detach()
            view.deleteLater()

    def show_session(self, name: str) -> bool:
        view = self._views.get(name)
        if view is None:
            return False
        self.tabs.setCurrentWidget(view)
        view.focus_terminal()
        return True

    def names(self) -> list[str]:
        return list(self._views)
