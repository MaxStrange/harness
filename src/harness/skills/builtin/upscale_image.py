"""Upscale an image 2x or 4x with Real-ESRGAN on the LLM machine."""

from __future__ import annotations

import base64
from pathlib import Path

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path, human_size
from harness.skills.builtin._generation import call_service, make_room, write_b64

INPUT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MAX_INPUT_BYTES = 50 * 1024 * 1024  # the service also caps the input at 24 megapixels


class UpscaleImageSkill(Skill):
    name = "upscale_image"
    description = (
        "Upscale (super-resolve) a photo or picture 2x or 4x with Real-ESRGAN on the LLM machine "
        "and show the result in the image viewer. It sharpens detail and removes JPEG artefacts. "
        "2x looks most natural and is the default; 4x is crisper but can smooth skin into a "
        "waxy look, so for portraits at 4x use strength about 0.7, which blends the original "
        "texture back in. Saved as a PNG next to the original (<name>_x2.png) unless you give "
        "a path; nothing is overwritten."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The image to upscale."},
            "scale": {"type": "integer", "enum": [2, 4], "default": 2},
            "strength": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 1.0,
                "description": "1 = the model's output; lower blends in a plain resize to keep "
                "natural texture.",
            },
            "output": {"type": "string", "description": "Optional .png file to save to."},
        },
        "required": ["path"],
    }
    timeout_s = 900  # may include swapping models in and out
    handoff_description = "The embedded image viewer shows the upscaled image."

    def approval_request(self, args, ctx):
        source = ctx.resolve(args["path"])
        request = self.protected_path_request(source, args, ctx)
        if request is not None:
            return request
        return self.protected_path_request(_destination(ctx, args), args, ctx, "write")

    def run(self, args, ctx: SkillContext) -> SkillResult:
        source = existing_path(ctx, args["path"], want="file")
        if source.suffix.lower() not in INPUT_SUFFIXES:
            raise SkillError(
                f"{source.name} is not a supported image ({', '.join(sorted(INPUT_SUFFIXES))})"
            )
        size = source.stat().st_size
        if size > MAX_INPUT_BYTES:
            raise SkillError(f"{source.name} is {human_size(size)}; the limit is 50 MB")
        scale = int(args.get("scale", 2))
        if scale not in (2, 4):
            raise SkillError("scale must be 2 or 4")
        strength = float(args.get("strength", 1.0))
        if not 0.0 <= strength <= 1.0:
            raise SkillError("strength must be between 0 and 1")
        target = _destination(ctx, args)
        if target.exists():
            raise SkillError(f"{target} already exists; pick another name (nothing is overwritten)")
        make_room(ctx, "superres")
        ctx.say(f"Upscaling {source.name} {scale}x...")
        payload = {
            "image_base64": base64.b64encode(source.read_bytes()).decode(),
            "scale": scale,
            "strength": strength,
        }
        result = call_service(ctx, "superres", "/upscale", payload, timeout_s=600)
        written = write_b64(target, result["png_base64"])
        handoff = Handoff.image(target)
        ctx.services.ui.open_embedded(handoff)
        return SkillResult(
            f"Upscaled {source.name} {scale}x to {target} ({result['width']}x{result['height']}, "
            f"{human_size(written)}, strength {strength:g}, {result['seconds']}s on the GPU). "
            "It is showing in the image viewer.",
            handoff,
            data={"path": str(target), "width": result["width"], "height": result["height"]},
        )


def _destination(ctx: SkillContext, args: dict) -> Path:
    """The requested .png, or <stem>_x<scale>.png next to the source (the first free name)."""
    if args.get("output"):
        path = ctx.resolve(args["output"])
        return path if path.suffix.lower() == ".png" else path.with_suffix(".png")
    source = ctx.resolve(args["path"])
    scale = int(args.get("scale", 2))
    path = source.with_name(f"{source.stem}_x{scale}.png")
    n = 2
    while path.exists():
        path = source.with_name(f"{source.stem}_x{scale}-{n}.png")
        n += 1
    return path
