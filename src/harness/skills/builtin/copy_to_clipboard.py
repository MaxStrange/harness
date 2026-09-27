"""S14: put text on the clipboard."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult


class CopyToClipboardSkill(Skill):
    name = "copy_to_clipboard"
    description = "Put text on the user's clipboard so they can paste it anywhere."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    handoff_description = "The user pastes the text wherever it is needed."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        ctx.services.ui.set_clipboard(args["text"])
        return SkillResult(
            f"Copied {len(args['text'])} characters to the clipboard.",
            Handoff.done("On the clipboard"),
        )
