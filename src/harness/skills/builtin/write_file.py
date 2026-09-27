"""S11: create a file or edit one. Approval shows a diff."""

from __future__ import annotations

import difflib

from harness.skills.base import (
    ApprovalRequest,
    Handoff,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
)


class WriteFileSkill(Skill):
    name = "write_file"
    description = (
        "Create or overwrite a file (action 'write' with content), or make a targeted edit "
        "(action 'edit': replace old_text with new_text; old_text must match exactly, once unless replace_all). "
        "The user approves the change as a diff."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["write", "edit"]},
            "path": {"type": "string"},
            "content": {"type": "string", "description": "For write: the whole new file content."},
            "old_text": {"type": "string", "description": "For edit: exact text to replace."},
            "new_text": {"type": "string", "description": "For edit: replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "For edit: replace every occurrence. Default false.",
            },
        },
        "required": ["action", "path"],
    }
    needs_approval = True
    handoff_description = "The changed file opens in the editor."

    def _plan(self, args, ctx) -> tuple[object, str, str]:
        path = ctx.resolve(args["path"])
        if path.is_dir():
            raise SkillError(f"{path} is a directory")
        before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if args["action"] == "write":
            if args.get("content") is None:
                raise SkillError("content is required for action 'write'")
            after = args["content"]
        else:
            old, new = args.get("old_text"), args.get("new_text")
            if old is None or new is None:
                raise SkillError("old_text and new_text are required for action 'edit'")
            if not path.exists():
                raise SkillError(f"{path} does not exist")
            count = before.count(old)
            if count == 0:
                raise SkillError(f"old_text was not found in {path}")
            if count > 1 and not args.get("replace_all"):
                raise SkillError(
                    f"old_text occurs {count} times in {path}; make it unique or set replace_all"
                )
            after = (
                before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
            )
        return path, before, after

    def approval_request(self, args, ctx):
        path, before, after = self._plan(args, ctx)
        label = str(path)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(True), after.splitlines(True), f"a/{label}", f"b/{label}"
            )
        )
        if not diff:
            diff = "(no change)"
        reason = None
        if ctx.services.path_policy is not None:
            reason = ctx.services.path_policy.denial_reason(path)
        verb = "Create" if not path.exists() else "Change"
        return ApprovalRequest(
            self.name, f"{verb} {path.name}?", diff, "diff", reason=reason, args=args
        )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path, before, after = self._plan(args, ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after, encoding="utf-8")
        added = after.count("\n") - before.count("\n")
        verb = "Wrote" if args["action"] == "write" else "Edited"
        return SkillResult(
            f"{verb} {path} ({len(after)} bytes, {added:+d} lines).", Handoff.editor(path)
        )
