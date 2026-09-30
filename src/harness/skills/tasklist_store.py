"""Task lists (S18): visible checklists, shared by the skills and the UI.

There are two kinds, each identified by a key: a session's own checklist
(``session:<id>``) and a project's list (``project:<id>``), which outlives any
one session in the project. Lists are read on first use through ``loader`` and
written through ``saver`` after every change, whoever made it (the model or the
user ticking a box in the panel).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

STATUSES = ("todo", "doing", "done")


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


def project_key(project_id: str) -> str:
    return f"project:{project_id}"


@dataclass
class Task:
    id: str
    text: str
    status: str = "todo"


@dataclass
class TaskList:
    tasks: list[Task] = field(default_factory=list)

    def render(self) -> str:
        if not self.tasks:
            return "(no tasks)"
        marks = {"todo": "[ ]", "doing": "[~]", "done": "[x]"}
        return "\n".join(f"{marks[t.status]} {t.id}: {t.text}" for t in self.tasks)

    def open_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.status != "done"]

    def to_json(self) -> str:
        return json.dumps([asdict(t) for t in self.tasks])

    @classmethod
    def from_json(cls, text: str | None) -> TaskList:
        try:
            return cls([Task(**item) for item in json.loads(text or "[]")])
        except (ValueError, TypeError) as exc:  # a damaged list must not break the session
            log.warning("unreadable task list, starting empty: %s", exc)
            return cls()


Loader = Callable[[str], "str | None"]
Saver = Callable[[str, str], None]


class TaskListStore:
    def __init__(self, loader: Loader | None = None, saver: Saver | None = None) -> None:
        self._lists: dict[str, TaskList] = {}
        self._lock = threading.RLock()
        self.loader = loader
        self.saver = saver
        self.listeners: list[Callable[[str, TaskList], None]] = []

    def _list(self, key: str) -> TaskList:
        """The list for ``key``, read from storage the first time. Call with the lock held."""
        tasks = self._lists.get(key)
        if tasks is None:
            text = None
            if self.loader is not None:
                try:
                    text = self.loader(key)
                except Exception:  # noqa: BLE001 - storage trouble: an empty list, logged
                    log.exception("loading task list %s failed", key)
            tasks = self._lists[key] = TaskList.from_json(text)
        return tasks

    def get(self, key: str) -> TaskList:
        with self._lock:
            return self._list(key)

    def forget(self, key: str) -> None:
        """Drop the cached copy (it is read again on next use)."""
        with self._lock:
            self._lists.pop(key, None)

    def add(self, key: str, text: str) -> Task:
        task = Task(id=uuid.uuid4().hex[:6], text=text)
        with self._lock:
            self._list(key).tasks.append(task)
        self._changed(key)
        return task

    def update(
        self, key: str, task_id: str, *, status: str | None = None, text: str | None = None
    ) -> Task | None:
        with self._lock:
            task = next((t for t in self._list(key).tasks if t.id == task_id), None)
            if task is None:
                return None
            if status is not None:
                task.status = status
            if text is not None:
                task.text = text
        self._changed(key)
        return task

    def remove(self, key: str, task_id: str) -> bool:
        with self._lock:
            tasks = self._list(key).tasks
            before = len(tasks)
            tasks[:] = [t for t in tasks if t.id != task_id]
            removed = len(tasks) != before
        if removed:
            self._changed(key)
        return removed

    def reorder(self, key: str, ordered_ids: list[str]) -> None:
        """Put the tasks in ``ordered_ids`` order; ids not listed keep their relative order at the end."""
        with self._lock:
            tasks = self._list(key).tasks
            by_id = {t.id: t for t in tasks}
            ordered = [by_id[i] for i in ordered_ids if i in by_id]
            rest = [t for t in tasks if t.id not in set(ordered_ids)]
            tasks[:] = ordered + rest
        self._changed(key)

    def move(self, source: str, target: str, task_id: str, index: int | None = None) -> Task | None:
        """Move a task from one list to another (session <-> project), at ``index`` or the end."""
        if source == target:
            return None
        with self._lock:
            tasks = self._list(source).tasks
            task = next((t for t in tasks if t.id == task_id), None)
            if task is None:
                return None
            tasks.remove(task)
            destination = self._list(target).tasks
            destination.insert(len(destination) if index is None else index, task)
        self._changed(source)
        self._changed(target)
        return task

    def clear(self, key: str) -> None:
        with self._lock:
            self._lists[key] = TaskList()
        self._changed(key)

    def _changed(self, key: str) -> None:
        current = self.get(key)
        if self.saver is not None:
            try:
                self.saver(key, current.to_json())
            except Exception:  # noqa: BLE001 - keep working in memory; the log says why
                log.exception("saving task list %s failed", key)
        for listener in list(self.listeners):
            listener(key, current)
