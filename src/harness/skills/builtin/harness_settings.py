"""Let the model change harness preferences the user asks for (the critter, for now)."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.ui.critter import CRITTERS

SETTINGS = {
    "critter": {"choices": list(CRITTERS), "description": "the sprite shown while the model works"},
}


class HarnessSettingsSkill(Skill):
    name = "harness_settings"
    description = (
        "Change a harness preference when the user asks, e.g. 'make the sprite a turtle'. "
        "Settings: critter (" + ", ".join(CRITTERS) + "). Action 'show' lists the current values."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "show"]},
            "setting": {"type": "string", "enum": sorted(SETTINGS)},
            "value": {"type": "string"},
        },
        "required": ["action"],
    }
    handoff_description = (
        "The change is visible in the window immediately and saved to the config file."
    )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        if args["action"] == "show":
            lines = [f"critter: {ctx.config.ui.critter} (choices: {', '.join(CRITTERS)})"]
            return SkillResult("\n".join(lines), Handoff.done("Shown"))
        setting = args.get("setting")
        value = (args.get("value") or "").strip().lower()
        if setting not in SETTINGS:
            raise SkillError(f"unknown setting {setting!r}; choose from {sorted(SETTINGS)}")
        choices = SETTINGS[setting]["choices"]
        if value not in choices:
            raise SkillError(f"{setting} must be one of {choices}, not {value!r}")
        error = ctx.services.ui.set_preference(setting, value)
        if error:
            return SkillResult.fail(error)
        return SkillResult(f"{setting} is now {value}.", Handoff.done(f"{setting}: {value}"))
