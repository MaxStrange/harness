"""Generate an image from a text prompt with the image model on the LLM machine."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import human_size
from harness.skills.builtin._generation import call_service, make_room, output_path, write_b64


class GenerateImageSkill(Skill):
    name = "generate_image"
    description = (
        "Generate an image from a text description (FLUX.1-schnell on the LLM machine) and show "
        "it in the image viewer. Describe the subject, style, composition and lighting in plain "
        "words. The first image after a while takes a minute or two while the model loads. "
        "Saved under ~/.harness/generated unless you give a path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to draw."},
            "width": {"type": "integer", "minimum": 256, "maximum": 1536, "default": 1024},
            "height": {"type": "integer", "minimum": 256, "maximum": 1536, "default": 1024},
            "seed": {"type": "integer", "description": "Repeat a seed to repeat an image."},
            "path": {"type": "string", "description": "Optional .png file to save to."},
        },
        "required": ["prompt"],
    }
    timeout_s = 900  # may include swapping models in and out
    handoff_description = "The embedded image viewer shows the image."

    def approval_request(self, args, ctx):
        if args.get("path"):
            return self.protected_path_request(ctx.resolve(args["path"]), args, ctx, "write")
        return None

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = output_path(ctx, args.get("path"), args["prompt"], ".png")
        width = _multiple_of_16(args.get("width", 1024))
        height = _multiple_of_16(args.get("height", 1024))
        make_room(ctx, "image")
        ctx.say("Generating the image...")
        payload = {"prompt": args["prompt"], "width": width, "height": height}
        if args.get("seed") is not None:
            payload["seed"] = args["seed"]
        result = call_service(ctx, "image", "/image", payload, timeout_s=600)
        size = write_b64(path, result["png_base64"])
        handoff = Handoff.image(path)
        ctx.services.ui.open_embedded(handoff)
        return SkillResult(
            f"Generated {path} ({width}x{height}, {human_size(size)}, seed {result['seed']}, "
            f"{result['seconds']}s). It is showing in the image viewer.",
            handoff,
            data={"path": str(path), "seed": result["seed"]},
        )


def _multiple_of_16(value: int) -> int:
    return max(256, min(1536, int(round(value / 16)) * 16))
