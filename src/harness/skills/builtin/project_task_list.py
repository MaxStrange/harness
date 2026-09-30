"""The project's task list: work that belongs to the project, shared by all its sessions."""

from __future__ import annotations

from harness.skills.builtin._tasks import ChecklistSkill, task_parameters


class ProjectTaskListSkill(ChecklistSkill):
    name = "project_task_list"
    scope = "project"
    description = (
        "The task list of this session's project: a backlog shared by every session in the "
        "project and kept between them (task_list is this session's own checklist). Use it "
        "for work that outlives this session. Actions: add (text), update (task_id, status "
        "todo/doing/done and/or text), remove (task_id), clear, show, move (task_id: to this "
        "session's checklist, e.g. when starting on it here). Only in a project."
    )
    parameters = task_parameters("")
