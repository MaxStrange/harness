"""The shared body of the two checklist skills: this session's list and the project's list."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.tasklist_store import STATUSES, project_key, session_key

ACTIONS = ["add", "update", "remove", "clear", "show", "move"]


def task_parameters(move_help: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "text": {"type": "string"},
            "task_id": {"type": "string"},
            "status": {"type": "string", "enum": list(STATUSES)},
            "index": {
                "type": "integer",
                "description": f"For move: where in the other list (0 = top). {move_help}",
            },
        },
        "required": ["action"],
    }


class ChecklistSkill(Skill):
    """A checklist skill; subclasses say which list is theirs and which is the other one."""

    scope = "session"  # "session" or "project"
    handoff_description = "The tasks panel inside the harness."

    def keys(self, ctx: SkillContext) -> tuple[str, str | None]:
        """This skill's list, and the list ``move`` sends tasks to (None if there is none)."""
        project = project_key(ctx.project_id) if ctx.project_id else None
        if self.scope == "project":
            if project is None:
                raise SkillError(
                    "this session is not in a project, so there is no project task list; "
                    "use task_list for this session's checklist"
                )
            return project, session_key(ctx.session_id)
        return session_key(ctx.session_id), project

    def run(self, args, ctx: SkillContext) -> SkillResult:
        store = ctx.need("task_lists")
        action = args["action"]
        key, other = self.keys(ctx)
        label = "project" if self.scope == "project" else "session"
        if action == "add":
            if not args.get("text"):
                raise SkillError("text is required to add a task")
            note = f"Added task {store.add(key, args['text']).id}."
        elif action == "update":
            if not args.get("task_id"):
                raise SkillError("task_id is required")
            task = store.update(
                key, args["task_id"], status=args.get("status"), text=args.get("text")
            )
            if task is None:
                raise SkillError(f"no task with id {args['task_id']!r} in the {label} list")
            note = f"Updated task {task.id}."
        elif action == "remove":
            if not store.remove(key, args.get("task_id") or ""):
                raise SkillError(f"no task with id {args.get('task_id')!r} in the {label} list")
            note = "Removed."
        elif action == "clear":
            store.clear(key)
            note = "Cleared."
        elif action == "move":
            if other is None:
                raise SkillError(
                    "this session is not in a project, so there is no project list to move to"
                )
            target = "session" if self.scope == "project" else "project"
            task = store.move(key, other, args.get("task_id") or "", args.get("index"))
            if task is None:
                raise SkillError(f"no task with id {args.get('task_id')!r} in the {label} list")
            note = f"Moved task {task.id} to the {target} list.\n{target.capitalize()} tasks:\n"
            note += store.get(other).render() + f"\n{label.capitalize()} tasks now:"
        else:
            note = f"Current {label} tasks:"
        handoff = Handoff.tasks()
        if action != "show":
            ctx.services.ui.open_embedded(handoff)
        return SkillResult(f"{note}\n{store.get(key).render()}", handoff)
