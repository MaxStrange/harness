"""The tasks panel (S18): the project's list above this session's checklist.

Tick a box to finish a task, drag within a list to reorder, drag from one list
to the other (or right-click) to move a task between the project and the
session. Every change goes through the TaskListStore, which saves it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from harness.skills.tasklist_store import TaskList, TaskListStore, project_key, session_key

PREFIX = {"todo": "", "doing": "(in progress) ", "done": ""}
ID_ROLE = Qt.ItemDataRole.UserRole


class _TaskList(QListWidget):
    """One checklist. Drops (from itself or the other list) are reported, never done by Qt."""

    dropped = Signal(object, str, int)  # source list, task id, index in this list

    def __init__(self) -> None:
        super().__init__()
        self.key: str | None = None  # the TaskListStore key this list shows
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def order(self) -> list[str]:
        return [self.item(i).data(ID_ROLE) for i in range(self.count())]

    def drop_index(self, event) -> int:
        target = self.itemAt(event.position().toPoint())
        if target is None:
            return self.count()
        row = self.row(target)
        below = self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem
        return row + 1 if below else row

    def dragEnterEvent(self, event) -> None:
        if isinstance(event.source(), _TaskList):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)  # the drop indicator
        if isinstance(event.source(), _TaskList):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        source = event.source()
        if not isinstance(source, _TaskList) or source.currentItem() is None:
            event.ignore()
            return
        task_id = source.currentItem().data(ID_ROLE)
        index = self.drop_index(event)
        event.setDropAction(Qt.DropAction.IgnoreAction)  # the store moves it; we re-render
        event.accept()
        self.dropped.emit(source, task_id, index)


class TaskListView(QWidget):
    def __init__(self, store: TaskListStore) -> None:
        super().__init__()
        self.store = store
        self.session_id: str | None = None
        self.project_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Tasks")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        self.project_title = QLabel("Project")
        self.project_title.setObjectName("sectionTitle")
        self.project_title.setToolTip(
            "The project's own list, kept between its sessions and shown to the model"
        )
        layout.addWidget(self.project_title)
        self.project_list = _TaskList()
        layout.addWidget(self.project_list, 1)
        self.project_summary = QLabel("")
        self.project_summary.setObjectName("status")
        layout.addWidget(self.project_summary)

        self.session_title = QLabel("This session")
        self.session_title.setObjectName("sectionTitle")
        layout.addWidget(self.session_title)
        self.list = _TaskList()  # this session's checklist (the name tests and callers know)
        layout.addWidget(self.list, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("status")
        layout.addWidget(self.summary)

        for widget in (self.project_list, self.list):
            widget.itemChanged.connect(lambda item, w=widget: self._item_changed(w, item))
            widget.dropped.connect(
                lambda source, tid, index, w=widget: self._dropped(w, source, tid, index)
            )
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(lambda pos, w=widget: self._menu(w, pos))
        self._show_project(False)

    # -- which lists -----------------------------------------------------------------

    def set_session(
        self, session_id: str, project_id: str | None = None, project_name: str | None = None
    ) -> None:
        self.session_id, self.project_id = session_id, project_id
        self.list.key = session_key(session_id)
        self.project_list.key = project_key(project_id) if project_id else None
        self.project_title.setText(f"Project: {project_name}" if project_name else "Project")
        self.session_title.setText("This session" if project_id else "Tasks for this session")
        self._show_project(project_id is not None)
        self.render(self.store.get(self.list.key))
        if self.project_list.key:
            self._render(
                self.project_list, self.project_summary, self.store.get(self.project_list.key)
            )

    def _show_project(self, on: bool) -> None:
        for widget in (
            self.project_title,
            self.project_list,
            self.project_summary,
            self.session_title,
        ):
            widget.setVisible(on)

    def on_store_changed(self, key: str, tasks: TaskList) -> None:
        if key == self.list.key:
            self.render(tasks)
        elif key == self.project_list.key:
            self._render(self.project_list, self.project_summary, tasks)

    # -- drawing ---------------------------------------------------------------------

    def render(self, tasks: TaskList) -> None:
        """Show this session's checklist."""
        self._render(self.list, self.summary, tasks)

    def _render(self, widget: _TaskList, summary: QLabel, tasks: TaskList) -> None:
        widget.blockSignals(True)
        widget.clear()
        for task in tasks.tasks:
            item = QListWidgetItem(f"{PREFIX[task.status]}{task.text}")
            item.setData(ID_ROLE, task.id)
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                & ~Qt.ItemFlag.ItemIsDropEnabled  # drop between items, never onto one
            )
            item.setCheckState(
                Qt.CheckState.Checked if task.status == "done" else Qt.CheckState.Unchecked
            )
            widget.addItem(item)
        widget.blockSignals(False)
        done = sum(1 for t in tasks.tasks if t.status == "done")
        other = "session" if widget is self.project_list else "project"
        hint = "drag to reorder" + (f" or to the {other} list" if self.project_list.key else "")
        summary.setText(
            f"{done}/{len(tasks.tasks)} done; {hint}" if tasks.tasks else "No tasks yet"
        )

    # -- editing ---------------------------------------------------------------------

    def _item_changed(self, widget: _TaskList, item: QListWidgetItem) -> None:
        if widget.key is None:
            return
        status = "done" if item.checkState() == Qt.CheckState.Checked else "todo"
        self.store.update(widget.key, item.data(ID_ROLE), status=status)

    def _dropped(self, target: _TaskList, source: _TaskList, task_id: str, index: int) -> None:
        if target.key is None or source.key is None:
            return
        if source is target:
            order = [t for t in target.order() if t != task_id]
            before = target.order().index(task_id)
            order.insert(index - (1 if before < index else 0), task_id)
            self.store.reorder(target.key, order)
        else:
            self.store.move(source.key, target.key, task_id, index)

    def _menu(self, widget: _TaskList, pos) -> None:
        item = widget.itemAt(pos)
        if item is None or widget.key is None:
            return
        other = self.list if widget is self.project_list else self.project_list
        menu = QMenu(self)
        if other.key is not None:
            label = "Move to this session" if widget is self.project_list else "Move to the project"
            menu.addAction(
                label, lambda: self.store.move(widget.key, other.key, item.data(ID_ROLE))
            )
        menu.addAction("Remove", lambda: self.store.remove(widget.key, item.data(ID_ROLE)))
        menu.exec(widget.viewport().mapToGlobal(pos))
