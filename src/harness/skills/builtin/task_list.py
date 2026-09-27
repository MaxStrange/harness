"""S18: a visible checklist for multi-step work."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.tasklist_store import STATUSES


class TaskListSkill(Skill):
    name = "task_list"
    description = (
        "Keep a checklist the user can see while you work through a multi-step task. "
        "Actions: add (text), update (task_id, status todo/doing/done and/or text), remove (task_id), clear, show."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "remove", "clear", "show"]},
            "text": {"type": "string"},
            "task_id": {"type": "string"},
            "status": {"type": "string", "enum": list(STATUSES)},
        },
        "required": ["action"],
    }
    handoff_description = "The checklist panel inside the harness."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        store = ctx.need("task_lists")
        action = args["action"]
        sid = ctx.session_id
        if action == "add":
            if not args.get("text"):
                raise SkillError("text is required to add a task")
            task = store.add(sid, args["text"])
            note = f"Added task {task.id}."
        elif action == "update":
            if not args.get("task_id"):
                raise SkillError("task_id is required")
            task = store.update(
                sid, args["task_id"], status=args.get("status"), text=args.get("text")
            )
            if task is None:
                raise SkillError(f"no task with id {args['task_id']!r}")
            note = f"Updated task {task.id}."
        elif action == "remove":
            if not store.remove(sid, args.get("task_id") or ""):
                raise SkillError(f"no task with id {args.get('task_id')!r}")
            note = "Removed."
        elif action == "clear":
            store.clear(sid)
            note = "Cleared."
        else:
            note = "Current tasks:"
        handoff = Handoff.tasks()
        if action != "show":
            ctx.services.ui.open_embedded(handoff)
        return SkillResult(f"{note}\n{store.get(sid).render()}", handoff)
