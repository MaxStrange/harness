"""Saved chat sessions (UI4, SH1, SH2): list, search, open, rename (double-click), delete."""

from __future__ import annotations

import time

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from harness.agent.store import SessionStore

ID_ROLE = Qt.ItemDataRole.UserRole
TITLE_ROLE = Qt.ItemDataRole.UserRole + 1


class _TitleDelegate(QStyledItemDelegate):
    """Edits only the title, not the two-line display text."""

    def __init__(self, panel: SessionsPanel) -> None:
        super().__init__(panel)
        self.panel = panel

    def createEditor(self, parent, option, index: QModelIndex):
        editor = QLineEdit(parent)
        editor.setPlaceholderText("Session title")
        return editor

    def setEditorData(self, editor: QLineEdit, index: QModelIndex) -> None:
        editor.setText(index.data(TITLE_ROLE) or "")
        editor.selectAll()

    def setModelData(self, editor: QLineEdit, model, index: QModelIndex) -> None:
        title = editor.text().strip()
        if title and title != index.data(TITLE_ROLE):
            self.panel.rename_requested.emit(index.data(ID_ROLE), title)


class SessionsPanel(QWidget):
    open_requested = Signal(str)
    new_requested = Signal()
    delete_requested = Signal(str)
    rename_requested = Signal(str, str)

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
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.list.setItemDelegate(_TitleDelegate(self))
        self.list.itemClicked.connect(self._clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)
        self.current_id: str | None = None
        self.refresh()

    # -- content -------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list from the store, keeping the current session selected."""
        query = self.search.text().strip()
        self.list.blockSignals(True)
        self.list.clear()
        if query:
            seen: set[str] = set()
            for hit in self.store.search(query):
                if hit.session_id in seen:
                    continue
                seen.add(hit.session_id)
                item = QListWidgetItem(f"{hit.title}\n    {hit.snippet}")
                item.setData(ID_ROLE, hit.session_id)
                item.setData(TITLE_ROLE, hit.title)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.list.addItem(item)
        else:
            for record in self.store.list_sessions():
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(record.updated))
                item = QListWidgetItem(f"{record.title}\n    {when}")
                item.setData(ID_ROLE, record.id)
                item.setData(TITLE_ROLE, record.title)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.list.addItem(item)
        self._select_current()
        self.list.blockSignals(False)

    def _find(self, session_id: str | None) -> QListWidgetItem | None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(ID_ROLE) == session_id:
                return item
        return None

    def _select_current(self) -> None:
        item = self._find(self.current_id)
        self.list.setCurrentItem(item)  # None clears the selection

    def set_current(self, session_id: str | None) -> None:
        """Select the open session without rebuilding the list (so clicks feel instant)."""
        self.current_id = session_id
        if session_id is not None and self._find(session_id) is None:
            self.refresh()
            return
        self.list.blockSignals(True)
        self._select_current()
        self.list.blockSignals(False)

    # -- interaction ---------------------------------------------------------------

    def _clicked(self, item: QListWidgetItem) -> None:
        session_id = item.data(ID_ROLE)
        if session_id and session_id != self.current_id:
            self.open_requested.emit(session_id)

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        session_id = item.data(ID_ROLE)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_requested.emit(session_id))
        menu.addAction("Rename", lambda: self.list.editItem(item))
        menu.addAction("Delete", lambda: self.delete_requested.emit(session_id))
        menu.exec(self.list.viewport().mapToGlobal(pos))
