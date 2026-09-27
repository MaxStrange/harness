"""Saved chat sessions (UI4, SH1, SH2): list, search, open, delete."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from harness.agent.store import SessionStore


class SessionsPanel(QWidget):
    open_requested = Signal(str)
    new_requested = Signal()
    delete_requested = Signal(str)

    def __init__(self, store: SessionStore) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        title = QLabel("Sessions")
        title.setObjectName("panelTitle")
        header.addWidget(title, 1)
        new_button = QPushButton("New")
        new_button.setObjectName("accent")
        new_button.clicked.connect(self.new_requested.emit)
        header.addWidget(new_button)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all sessions...")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._activated)
        self.list.itemClicked.connect(self._activated)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)
        self.current_id: str | None = None
        self.refresh()

    def refresh(self) -> None:
        query = self.search.text().strip()
        self.list.clear()
        if query:
            seen: set[str] = set()
            for hit in self.store.search(query):
                if hit.session_id in seen:
                    continue
                seen.add(hit.session_id)
                item = QListWidgetItem(f"{hit.title}\n    {hit.snippet}")
                item.setData(Qt.ItemDataRole.UserRole, hit.session_id)
                self.list.addItem(item)
            return
        for record in self.store.list_sessions():
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(record.updated))
            item = QListWidgetItem(f"{record.title}\n    {when}")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            if record.id == self.current_id:
                item.setSelected(True)
            self.list.addItem(item)

    def set_current(self, session_id: str | None) -> None:
        self.current_id = session_id
        self.refresh()

    def _activated(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id and session_id != self.current_id:
            self.open_requested.emit(session_id)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        session_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_requested.emit(session_id))
        menu.addAction("Delete", lambda: self.delete_requested.emit(session_id))
        menu.exec(self.list.viewport().mapToGlobal(pos))
