"""S18: a visible checklist for multi-step work in this session."""

from __future__ import annotations

from harness.skills.builtin._tasks import ChecklistSkill, task_parameters


class TaskListSkill(ChecklistSkill):
    name = "task_list"
    scope = "session"
    description = (
        "Keep a checklist the user can see while you work through a multi-step task in this "
        "session. Actions: add (text), update (task_id, status todo/doing/done and/or text), "
        "remove (task_id), clear, show, move (task_id: to the project's list, see "
        "project_task_list)."
    )
    parameters = task_parameters("Only in a project.")
