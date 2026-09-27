"""S19: desktop notification."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult


class NotifySkill(Skill):
    name = "notify"
    description = "Send a desktop notification, e.g. when a long task is finished. Clicking it brings the harness to the front."
    parameters = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["title"],
    }
    handoff_description = "Clicking the notification brings up the session."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        ctx.services.ui.notify(args["title"], args.get("body") or "")
        return SkillResult("Notification sent.", Handoff.done("Notified"))
