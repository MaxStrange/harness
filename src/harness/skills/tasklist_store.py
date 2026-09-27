"""Task lists (S18): a visible checklist per session, shared by the skill and the UI."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

STATUSES = ("todo", "doing", "done")


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

    def to_json(self) -> str:
        return json.dumps([asdict(t) for t in self.tasks])

    @classmethod
    def from_json(cls, text: str) -> TaskList:
        return cls([Task(**item) for item in json.loads(text or "[]")])


class TaskListStore:
    def __init__(self) -> None:
        self._lists: dict[str, TaskList] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[str, TaskList], None]] = []

    def get(self, session_id: str) -> TaskList:
        with self._lock:
            return self._lists.setdefault(session_id, TaskList())

    def load(self, session_id: str, text: str | None) -> None:
        with self._lock:
            self._lists[session_id] = TaskList.from_json(text) if text else TaskList()
        self._notify(session_id)

    def add(self, session_id: str, text: str) -> Task:
        task = Task(id=uuid.uuid4().hex[:6], text=text)
        with self._lock:
            self._lists.setdefault(session_id, TaskList()).tasks.append(task)
        self._notify(session_id)
        return task

    def update(
        self, session_id: str, task_id: str, *, status: str | None = None, text: str | None = None
    ) -> Task | None:
        with self._lock:
            for task in self._lists.setdefault(session_id, TaskList()).tasks:
                if task.id == task_id:
                    if status is not None:
                        task.status = status
                    if text is not None:
                        task.text = text
                    found = task
                    break
            else:
                return None
        self._notify(session_id)
        return found

    def remove(self, session_id: str, task_id: str) -> bool:
        with self._lock:
            tasks = self._lists.setdefault(session_id, TaskList()).tasks
            before = len(tasks)
            tasks[:] = [t for t in tasks if t.id != task_id]
            removed = len(tasks) != before
        if removed:
            self._notify(session_id)
        return removed

    def reorder(self, session_id: str, ordered_ids: list[str]) -> None:
        """Put the tasks in ``ordered_ids`` order; ids not listed keep their relative order at the end."""
        with self._lock:
            tasks = self._lists.setdefault(session_id, TaskList()).tasks
            by_id = {t.id: t for t in tasks}
            ordered = [by_id[i] for i in ordered_ids if i in by_id]
            rest = [t for t in tasks if t.id not in set(ordered_ids)]
            tasks[:] = ordered + rest
        self._notify(session_id)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._lists[session_id] = TaskList()
        self._notify(session_id)

    def _notify(self, session_id: str) -> None:
        current = self.get(session_id)
        for listener in list(self.listeners):
            listener(session_id, current)
