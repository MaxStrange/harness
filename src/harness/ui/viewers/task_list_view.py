"""The checklist panel (S18)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from harness.skills.tasklist_store import TaskList, TaskListStore

PREFIX = {"todo": "", "doing": "(in progress) ", "done": ""}


class TaskListView(QWidget):
    def __init__(self, store: TaskListStore) -> None:
        super().__init__()
        self.store = store
        self.session_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Tasks")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._item_changed)
        layout.addWidget(self.list)
        self.summary = QLabel("")
        self.summary.setObjectName("status")
        layout.addWidget(self.summary)

    def set_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.render(self.store.get(session_id))

    def on_store_changed(self, session_id: str, tasks: TaskList) -> None:
        if session_id == self.session_id:
            self.render(tasks)

    def render(self, tasks: TaskList) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for task in tasks.tasks:
            item = QListWidgetItem(f"{PREFIX[task.status]}{task.text}")
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if task.status == "done" else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.list.blockSignals(False)
        done = sum(1 for t in tasks.tasks if t.status == "done")
        self.summary.setText(f"{done}/{len(tasks.tasks)} done" if tasks.tasks else "No tasks yet")

    def _item_changed(self, item: QListWidgetItem) -> None:
        if self.session_id is None:
            return
        status = "done" if item.checkState() == Qt.CheckState.Checked else "todo"
        self.store.update(self.session_id, item.data(Qt.ItemDataRole.UserRole), status=status)
