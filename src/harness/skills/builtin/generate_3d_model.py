"""Generate a 3D model (GLB mesh) from an image or a text prompt on the LLM machine."""

from __future__ import annotations

import base64

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path, human_size
from harness.skills.builtin._generation import call_service, make_room, output_path, write_b64

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class Generate3DModelSkill(Skill):
    name = "generate_3d_model"
    description = (
        "Make a 3D model (an untextured GLB mesh) with Hunyuan3D-2mini on the LLM machine, from "
        "an image file or, with 'prompt', from a text description (an image is generated first). "
        "Works best on one object, whole and centred, on a plain background. A preview is shown "
        "in the image viewer and the GLB opens in the harness's 3D viewer. Loading the models can "
        "take a few minutes. Saved under ~/.harness/generated unless you give a path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "Image of the object (png/jpg/webp)."},
            "prompt": {
                "type": "string",
                "description": "Describe the object instead of giving an image.",
            },
            "path": {"type": "string", "description": "Optional .glb file to save to."},
            "max_faces": {"type": "integer", "minimum": 1000, "maximum": 500000, "default": 40000},
            "seed": {"type": "integer"},
        },
    }
    timeout_s = 1500  # up to two model swaps plus two jobs
    handoff_description = "The GLB opens in the harness's 3D viewer."

    def approval_request(self, args, ctx):
        for key, action in (("image", "read"), ("path", "write")):
            if args.get(key):
                request = self.protected_path_request(ctx.resolve(args[key]), args, ctx, action)
                if request is not None:
                    return request
        return None

    def run(self, args, ctx: SkillContext) -> SkillResult:
        has_image, has_prompt = bool(args.get("image")), bool(args.get("prompt"))
        if has_image == has_prompt:
            raise SkillError("give exactly one of 'image' (a file) or 'prompt' (a description)")
        hint = args.get("prompt") or ctx.resolve(args["image"]).stem
        glb = output_path(ctx, args.get("path"), hint, ".glb")
        notes = []
        if has_image:
            source = existing_path(ctx, args["image"], want="file")
            if source.suffix.lower() not in IMAGE_SUFFIXES:
                raise SkillError(f"{source.name} is not a png, jpg or webp image")
            picture = source.read_bytes()
        else:
            make_room(ctx, "image")
            ctx.say("Generating a reference image...")
            prompt = (
                f"{args['prompt']}, a single object, whole and centred, three-quarter view, "
                "plain white background, soft even studio lighting, no shadow"
            )
            payload = {"prompt": prompt, "width": 1024, "height": 1024}
            if args.get("seed") is not None:
                payload["seed"] = args["seed"]
            reference = call_service(ctx, "image", "/image", payload, timeout_s=600)
            source = glb.with_name(glb.stem + "-reference.png")
            write_b64(source, reference["png_base64"])
            picture = base64.b64decode(reference["png_base64"])
            notes.append(f"reference image {source}")
        make_room(ctx, "mesh")
        ctx.say("Building the 3D model...")
        payload = {
            "image_base64": base64.b64encode(picture).decode(),
            "max_faces": args.get("max_faces", 40000),
        }
        if args.get("seed") is not None:
            payload["seed"] = args["seed"]
        result = call_service(ctx, "mesh", "/mesh", payload, timeout_s=900)
        size = write_b64(glb, result["glb_base64"])
        if result.get("preview_png_base64"):
            preview = glb.with_name(glb.stem + "-preview.png")
            write_b64(preview, result["preview_png_base64"])
            notes.append(f"preview image {preview}")
        summary = (
            f"Generated {glb} ({human_size(size)}, {result['vertices']} vertices, "
            f"{result['faces']} faces, seed {result['seed']}, {result['seconds']}s), "
            f"from {source.name}."
        )
        if notes:
            summary += " Also: " + "; ".join(notes) + "."
        handoff = Handoff.model(glb)
        ctx.services.ui.open_embedded(handoff)  # straight into the 3D viewer
        summary += " It is showing in the 3D viewer."
        return SkillResult(
            summary,
            handoff,
            data={"path": str(glb), "seed": result["seed"]},
        )
