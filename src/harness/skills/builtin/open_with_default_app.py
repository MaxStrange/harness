"""S13: open a file or URL in its default application. The skill is the handoff."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult


class OpenWithDefaultAppSkill(Skill):
    name = "open_with_default_app"
    description = "Open a file, folder or URL in the user's default application for it (browser, PDF viewer, image viewer, file manager...)."
    parameters = {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "A file path or a URL."}},
        "required": ["target"],
    }
    handoff_description = "Opening the target is the handoff itself."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        target = args["target"].strip()
        if not target:
            raise SkillError("target is empty")
        if "://" not in target:
            path = ctx.resolve(target)
            if not path.exists():
                raise SkillError(f"{path} does not exist")
            target = str(path)
        handoff = Handoff.default_app(target)
        error = ctx.services.ui.open_external(handoff)
        if error:
            return SkillResult.fail(error, handoff)
        return SkillResult(
            f"Opened {target} in its default application.", Handoff.done(f"Opened {target}")
        )
