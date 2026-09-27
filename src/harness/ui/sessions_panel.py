"""Saved chat sessions (UI4, SH1, SH2), grouped by project: list, search, open, rename, move."""

from __future__ import annotations

import time

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QFont
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
KIND_ROLE = Qt.ItemDataRole.UserRole + 2  # "session" | "project"
NO_PROJECT = "__none__"


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
    new_project_requested = Signal()
    edit_project_requested = Signal(str)
    delete_project_requested = Signal(str)
    move_requested = Signal(str, object)  # session id, project id or None
    global_context_requested = Signal()

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
        new_button.setToolTip("New session in the current project")
        new_button.clicked.connect(self.new_requested.emit)
        header.addWidget(new_button)
        more = QPushButton("...")
        more.setFixedWidth(32)
        more.setToolTip("Projects and context")
        menu = QMenu(more)
        menu.addAction("New project...", self.new_project_requested.emit)
        menu.addAction("Global context...", self.global_context_requested.emit)
        more.setMenu(menu)
        header.addWidget(more)
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
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)
        self.current_id: str | None = None
        self.current_project_id: str | None = None
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
                item = self._session_item(hit.session_id, hit.title, hit.snippet, editable=False)
                self.list.addItem(item)
        else:
            sessions = self.store.list_sessions()
            projects = self.store.list_projects()
            by_project: dict[str | None, list] = {p.id: [] for p in projects}
            by_project[None] = []
            for record in sessions:
                by_project.setdefault(
                    record.project_id if record.project_id in by_project else None, []
                ).append(record)
            for project in projects:
                self.list.addItem(
                    self._project_item(project.id, project.name, len(by_project[project.id]))
                )
                for record in by_project[project.id]:
                    self.list.addItem(
                        self._session_item(record.id, record.title, _when(record.updated))
                    )
            if projects and by_project[None]:
                self.list.addItem(
                    self._project_item(NO_PROJECT, "No project", len(by_project[None]))
                )
            for record in by_project[None]:
                self.list.addItem(
                    self._session_item(record.id, record.title, _when(record.updated))
                )
        self._select_current()
        self.list.blockSignals(False)

    def _session_item(
        self, session_id: str, title: str, second_line: str, editable: bool = True
    ) -> QListWidgetItem:
        item = QListWidgetItem(f"{title}\n    {second_line}")
        item.setData(ID_ROLE, session_id)
        item.setData(TITLE_ROLE, title)
        item.setData(KIND_ROLE, "session")
        flags = (
            item.flags() | Qt.ItemFlag.ItemIsEditable
            if editable
            else item.flags() & ~Qt.ItemFlag.ItemIsEditable
        )
        item.setFlags(flags)
        return item

    def _project_item(self, project_id: str, name: str, count: int) -> QListWidgetItem:
        item = QListWidgetItem(f"{name}  ({count})")
        item.setData(ID_ROLE, project_id)
        item.setData(TITLE_ROLE, name)
        item.setData(KIND_ROLE, "project")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a header: not selectable, not editable
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        item.setToolTip("Double-click to edit the project's context; right-click for more")
        return item

    def _find(self, session_id: str | None) -> QListWidgetItem | None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(KIND_ROLE) == "session" and item.data(ID_ROLE) == session_id:
                return item
        return None

    def _select_current(self) -> None:
        self.list.setCurrentItem(self._find(self.current_id))

    def set_current(self, session_id: str | None, project_id: str | None = None) -> None:
        """Select the open session without rebuilding the list (so clicks feel instant)."""
        self.current_id = session_id
        self.current_project_id = project_id
        if session_id is not None and self._find(session_id) is None:
            self.refresh()
            return
        self.list.blockSignals(True)
        self._select_current()
        self.list.blockSignals(False)

    # -- interaction ---------------------------------------------------------------

    def _clicked(self, item: QListWidgetItem) -> None:
        if item.data(KIND_ROLE) != "session":
            return
        session_id = item.data(ID_ROLE)
        if session_id and session_id != self.current_id:
            self.open_requested.emit(session_id)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        if item.data(KIND_ROLE) == "project" and item.data(ID_ROLE) != NO_PROJECT:
            self.edit_project_requested.emit(item.data(ID_ROLE))

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction("New project...", self.new_project_requested.emit)
            menu.addAction("Global context...", self.global_context_requested.emit)
        elif item.data(KIND_ROLE) == "project":
            project_id = item.data(ID_ROLE)
            if project_id != NO_PROJECT:
                menu.addAction(
                    "Edit project context...", lambda: self.edit_project_requested.emit(project_id)
                )
                menu.addAction(
                    "Delete project (keeps its sessions)",
                    lambda: self.delete_project_requested.emit(project_id),
                )
            menu.addAction("New project...", self.new_project_requested.emit)
        else:
            session_id = item.data(ID_ROLE)
            menu.addAction("Open", lambda: self.open_requested.emit(session_id))
            menu.addAction("Rename", lambda: self.list.editItem(item))
            move = menu.addMenu("Move to project")
            for project in self.store.list_projects():
                move.addAction(
                    project.name, lambda pid=project.id: self.move_requested.emit(session_id, pid)
                )
            move.addAction("No project", lambda: self.move_requested.emit(session_id, None))
            move.addSeparator()
            move.addAction("New project...", self.new_project_requested.emit)
            menu.addAction("Delete", lambda: self.delete_requested.emit(session_id))
        menu.exec(self.list.viewport().mapToGlobal(pos))


def _when(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
