"""Saved chat sessions (UI4, SH1, SH2): a tree of projects and their sessions, with search.

The order is yours: drag a session between others to place it, onto a project
header to move it into that project, and drag project headers to reorder them.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from harness.agent.store import ANY_PROJECT, SessionStore

ID_ROLE = Qt.ItemDataRole.UserRole
TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2  # "session" | "project"
NO_PROJECT = "__none__"
SINCE_CHOICES = [
    ("Any time", None),
    ("Today", 1),
    ("Last 7 days", 7),
    ("Last 30 days", 30),
    ("Last year", 365),
]


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


class _SessionTree(QTreeWidget):
    """A tree whose drops are interpreted by the panel instead of moving items itself."""

    def __init__(self, panel: SessionsPanel) -> None:
        super().__init__()
        self.panel = panel
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def _drop_target(self, event) -> tuple[QTreeWidgetItem | None, str]:
        target = self.itemAt(event.position().toPoint())
        where = {
            QAbstractItemView.DropIndicatorPosition.AboveItem: "above",
            QAbstractItemView.DropIndicatorPosition.BelowItem: "below",
            QAbstractItemView.DropIndicatorPosition.OnItem: "on",
        }.get(self.dropIndicatorPosition(), "end")
        return target, where

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)  # drop indicator and auto-scroll
        target, where = self._drop_target(event)
        if self.panel.plan_drop(self.currentItem(), target, where) is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        target, where = self._drop_target(event)
        dragged = self.currentItem()
        # The panel rewrites the store and rebuilds the tree; Qt must not move the items.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.panel.drop(dragged, target, where)


class SessionsPanel(QWidget):
    open_requested = Signal(str)
    new_requested = Signal()
    delete_requested = Signal(str)
    rename_requested = Signal(str, str)
    new_project_requested = Signal(object)  # a session id to move into the new project, or None
    edit_project_requested = Signal(str)
    delete_project_requested = Signal(str)
    move_requested = Signal(str, object)  # session id, project id or None
    global_context_requested = Signal()

    def __init__(self, store: SessionStore) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.store = store
        self.current_id: str | None = None
        self.current_project_id: str | None = None
        self._collapsed: set[str] = set()
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
        menu.addAction("New project...", lambda: self.new_project_requested.emit(None))
        menu.addAction("Global context...", self.global_context_requested.emit)
        more.setMenu(menu)
        header.addWidget(more)
        layout.addLayout(header)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all sessions...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        search_row.addWidget(self.search, 1)
        self.advanced_button = QPushButton("Advanced")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        search_row.addWidget(self.advanced_button)
        layout.addLayout(search_row)

        self.advanced = QWidget()
        adv = QVBoxLayout(self.advanced)
        adv.setContentsMargins(0, 0, 0, 0)
        row1 = QHBoxLayout()
        self.project_filter = QComboBox()
        self.project_filter.setToolTip("Only sessions in this project")
        row1.addWidget(self.project_filter, 1)
        self.role_filter = QComboBox()
        self.role_filter.addItems(["Anyone", "Me", "Assistant"])
        self.role_filter.setToolTip("Who said it")
        row1.addWidget(self.role_filter)
        adv.addLayout(row1)
        row2 = QHBoxLayout()
        self.since_filter = QComboBox()
        for label, _days in SINCE_CHOICES:
            self.since_filter.addItem(label)
        row2.addWidget(self.since_filter)
        self.regex_box = QCheckBox("Regex")
        self.regex_box.setToolTip("Treat the search text as a Python regular expression")
        row2.addWidget(self.regex_box)
        self.case_box = QCheckBox("Match case")
        row2.addWidget(self.case_box)
        row2.addStretch(1)
        adv.addLayout(row2)
        self.search_status = QLabel("")
        self.search_status.setObjectName("status")
        adv.addWidget(self.search_status)
        self.advanced.setVisible(False)
        layout.addWidget(self.advanced)
        for widget in (self.project_filter, self.role_filter, self.since_filter):
            widget.currentIndexChanged.connect(self.refresh)
        for box in (self.regex_box, self.case_box):
            box.toggled.connect(self.refresh)

        self.tree = _SessionTree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(18)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setItemDelegate(_TitleDelegate(self))
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemExpanded.connect(lambda item: self._remember_collapse(item, False))
        self.tree.itemCollapsed.connect(lambda item: self._remember_collapse(item, True))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree)
        self.refresh()

    # -- content -------------------------------------------------------------

    def _toggle_advanced(self, on: bool) -> None:
        self.advanced.setVisible(on)
        if on:
            self._fill_project_filter()
        self.refresh()

    def _fill_project_filter(self) -> None:
        current = self.project_filter.currentData()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem("All projects", "__any__")
        for project in self.store.list_projects():
            self.project_filter.addItem(project.name, project.id)
        self.project_filter.addItem("No project", NO_PROJECT)
        index = self.project_filter.findData(current) if current is not None else -1
        self.project_filter.setCurrentIndex(index if index >= 0 else 0)
        self.project_filter.blockSignals(False)

    def search_options(self) -> dict:
        """The store.search keyword arguments for the current advanced settings."""
        if not self.advanced_button.isChecked():
            return {}
        project = self.project_filter.currentData()
        project_id = (
            ANY_PROJECT
            if project in (None, "__any__")
            else None
            if project == NO_PROJECT
            else project
        )
        role = {1: "user", 2: "assistant"}.get(self.role_filter.currentIndex())
        days = SINCE_CHOICES[self.since_filter.currentIndex()][1]
        return {
            "project_id": project_id,
            "role": role,
            "regex": self.regex_box.isChecked(),
            "case_sensitive": self.case_box.isChecked(),
            "since": time.time() - days * 86400 if days else None,
        }

    def refresh(self) -> None:
        """Rebuild the tree from the store, keeping the current session selected."""
        query = self.search.text().strip()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.setDragEnabled(not query)  # search results are not the list you arrange
        if query:
            self._fill_search(query)
        else:
            self._fill_projects()
            self.search_status.setText("")
        self._select_current()
        self.tree.blockSignals(False)

    def _fill_search(self, query: str) -> None:
        try:
            hits = self.store.search(query, **self.search_options())
        except ValueError as exc:
            self.search_status.setText(str(exc))
            return
        seen: dict[str, QTreeWidgetItem] = {}
        for hit in hits:
            if hit.session_id in seen:
                continue
            item = self._session_item(hit.session_id, hit.title, hit.snippet, editable=False)
            self.tree.addTopLevelItem(item)
            seen[hit.session_id] = item
        self.search_status.setText(f"{len(seen)} session(s) match" if seen else "No matches")

    def _fill_projects(self) -> None:
        sessions = self.store.list_sessions()
        projects = self.store.list_projects()
        by_project: dict[str | None, list] = {p.id: [] for p in projects}
        by_project[None] = []
        for record in sessions:
            key = record.project_id if record.project_id in by_project else None
            by_project[key].append(record)
        for project in projects:
            header = self._project_item(project.id, project.name, len(by_project[project.id]))
            self.tree.addTopLevelItem(header)
            for record in by_project[project.id]:
                header.addChild(self._session_item(record.id, record.title, _when(record.updated)))
            header.setExpanded(project.id not in self._collapsed)
        loose = by_project[None]
        if projects:  # even when empty: dropping a session here takes it out of its project
            header = self._project_item(NO_PROJECT, "No project", len(loose))
            self.tree.addTopLevelItem(header)
            for record in loose:
                header.addChild(self._session_item(record.id, record.title, _when(record.updated)))
            header.setExpanded(NO_PROJECT not in self._collapsed)
        elif loose:
            for record in loose:
                self.tree.addTopLevelItem(
                    self._session_item(record.id, record.title, _when(record.updated))
                )

    def _session_item(
        self, session_id: str, title: str, second_line: str, editable: bool = True
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{title}\n{second_line}"])
        item.setData(0, ID_ROLE, session_id)
        item.setData(0, TITLE_ROLE, title)
        item.setData(0, KIND_ROLE, "session")
        flags = (
            item.flags() | Qt.ItemFlag.ItemIsEditable
            if editable
            else item.flags() & ~Qt.ItemFlag.ItemIsEditable
        )
        # Dropping is between sessions (above/below), never onto one.
        item.setFlags((flags | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
        return item

    def _project_item(self, project_id: str, name: str, count: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{name}  ({count})"])
        item.setData(0, ID_ROLE, project_id)
        item.setData(0, TITLE_ROLE, name)
        item.setData(0, KIND_ROLE, "project")
        # A header: not selectable or editable; sessions drop onto it; real projects drag.
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
        if project_id != NO_PROJECT:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        item.setFlags(flags)
        font = QFont()
        font.setBold(True)
        item.setFont(0, font)
        item.setToolTip(
            0,
            "Click the arrow to fold; double-click to edit the project; drag to reorder; "
            "drop sessions here to move them in; right-click for more",
        )
        return item

    def _remember_collapse(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        if item.data(0, KIND_ROLE) == "project":
            if collapsed:
                self._collapsed.add(item.data(0, ID_ROLE))
            else:
                self._collapsed.discard(item.data(0, ID_ROLE))

    def _find(self, session_id: str | None) -> QTreeWidgetItem | None:
        if session_id is None:
            return None
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, KIND_ROLE) == "session" and top.data(0, ID_ROLE) == session_id:
                return top
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, ID_ROLE) == session_id:
                    return child
        return None

    def _select_current(self) -> None:
        item = self._find(self.current_id)
        self.tree.setCurrentItem(item)
        if item is not None and item.parent() is not None and not item.parent().isExpanded():
            item.parent().setExpanded(True)

    def set_current(self, session_id: str | None, project_id: str | None = None) -> None:
        """Select the open session without rebuilding the tree (so clicks feel instant)."""
        self.current_id = session_id
        self.current_project_id = project_id
        if session_id is not None and self._find(session_id) is None:
            self.refresh()
            return
        self.tree.blockSignals(True)
        self._select_current()
        self.tree.blockSignals(False)

    # -- arranging by drag and drop -------------------------------------------------

    def _groups(self) -> tuple[list[str | None], dict[str | None, list[str]]]:
        """The tree as shown: project ids in order (None = no project) and each one's sessions."""
        order: list[str | None] = []
        groups: dict[str | None, list[str]] = {}
        loose: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, KIND_ROLE) == "session":
                loose.append(top.data(0, ID_ROLE))
                continue
            key = None if top.data(0, ID_ROLE) == NO_PROJECT else top.data(0, ID_ROLE)
            order.append(key)
            groups[key] = [top.child(j).data(0, ID_ROLE) for j in range(top.childCount())]
        if loose or None not in groups:
            groups.setdefault(None, []).extend(loose)
            if None not in order:
                order.append(None)
        return order, groups

    @staticmethod
    def _key(item: QTreeWidgetItem) -> str | None:
        """The project an item stands for, or holds it (None = no project)."""
        header = item if item.data(0, KIND_ROLE) == "project" else item.parent()
        if header is None:
            return None
        project_id = header.data(0, ID_ROLE)
        return None if project_id == NO_PROJECT else project_id

    def plan_drop(
        self, dragged: QTreeWidgetItem | None, target: QTreeWidgetItem | None, where: str
    ) -> tuple | None:
        """What a drop would do, or None if it is not allowed.

        ("session", session_id, project_id, index) puts a session at ``index`` in that project;
        ("projects", [project ids]) is the new order of the project headers.
        """
        if dragged is None or self.search.text().strip():
            return None
        order, groups = self._groups()
        if dragged.data(0, KIND_ROLE) == "session":
            session_id = dragged.data(0, ID_ROLE)
            if target is None:
                if any(k is not None for k in order):  # between headers: nowhere sensible
                    return None
                return ("session", session_id, None, len(groups[None]))
            if target.data(0, KIND_ROLE) == "project":
                key = self._key(target)
                if where == "above":  # the gap above a header ends the previous project
                    position = order.index(key)
                    if position > 0:
                        previous = order[position - 1]
                        return ("session", session_id, previous, len(groups[previous]))
                return ("session", session_id, key, 0)
            key = self._key(target)
            index = groups[key].index(target.data(0, ID_ROLE)) + (1 if where != "above" else 0)
            return ("session", session_id, key, index)
        project_id = dragged.data(0, ID_ROLE)
        if project_id == NO_PROJECT:
            return None
        projects = [k for k in order if k is not None]
        if target is None or self._key(target) is None:
            index = len(projects)  # the end, still above "No project"
        else:
            key = self._key(target)
            below = where != "above" or target.data(0, KIND_ROLE) == "session"
            index = projects.index(key) + (1 if below else 0)
        moved = [p for p in projects if p != project_id]
        index -= sum(1 for p in projects[:index] if p == project_id)
        moved.insert(index, project_id)
        return ("projects", moved)

    def drop(
        self, dragged: QTreeWidgetItem | None, target: QTreeWidgetItem | None, where: str
    ) -> bool:
        """Carry out a drop: rewrite the order in the store and rebuild the tree."""
        plan = self.plan_drop(dragged, target, where)
        if plan is None:
            return False
        if plan[0] == "projects":
            self.store.reorder_projects(plan[1])
            self.refresh()
            return True
        _, session_id, project_id, index = plan
        _, groups = self._groups()
        source = self._key(dragged)
        ordered = list(groups.get(project_id, []))
        if session_id in ordered:
            if ordered.index(session_id) < index:
                index -= 1
            ordered.remove(session_id)
        ordered.insert(index, session_id)
        if source != project_id:
            # Through the window, so the session also adopts the project's root directory.
            self.move_requested.emit(session_id, project_id)
        self.store.reorder_sessions(project_id, ordered)
        self.refresh()
        return True

    # -- interaction ---------------------------------------------------------------

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, KIND_ROLE) != "session":
            return
        session_id = item.data(0, ID_ROLE)
        if session_id and session_id != self.current_id:
            self.open_requested.emit(session_id)

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "project" and item.data(0, ID_ROLE) != NO_PROJECT:
            self.edit_project_requested.emit(item.data(0, ID_ROLE))
        elif kind == "session" and item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.tree.editItem(item, 0)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction("New project...", lambda: self.new_project_requested.emit(None))
            menu.addAction("Global context...", self.global_context_requested.emit)
        elif item.data(0, KIND_ROLE) == "project":
            project_id = item.data(0, ID_ROLE)
            if project_id != NO_PROJECT:
                menu.addAction(
                    "Edit project context...", lambda: self.edit_project_requested.emit(project_id)
                )
                menu.addAction(
                    "Delete project (keeps its sessions)",
                    lambda: self.delete_project_requested.emit(project_id),
                )
            menu.addAction("New project...", lambda: self.new_project_requested.emit(None))
        else:
            session_id = item.data(0, ID_ROLE)
            menu.addAction("Open", lambda: self.open_requested.emit(session_id))
            menu.addAction("Rename", lambda: self.tree.editItem(item, 0))
            move = menu.addMenu("Move to project")
            for project in self.store.list_projects():
                move.addAction(
                    project.name, lambda pid=project.id: self.move_requested.emit(session_id, pid)
                )
            move.addAction("No project", lambda: self.move_requested.emit(session_id, None))
            move.addSeparator()
            move.addAction("New project...", lambda: self.new_project_requested.emit(session_id))
            menu.addAction("Delete", lambda: self.delete_requested.emit(session_id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))


def _when(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
