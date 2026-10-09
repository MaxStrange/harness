"""Show a 3D model (GLB, glTF, STL, OBJ) in the harness's embedded 3D viewer."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path, human_size

MODEL_SUFFIXES = (".glb", ".gltf", ".stl", ".obj")


class View3DModelSkill(Skill):
    name = "view_3d_model"
    description = (
        "Show a 3D model file to the user in the harness's 3D viewer (GLB, glTF, STL or OBJ; "
        "not STEP). For a quick look at a part: the user rotates with the left mouse button, "
        "pans with Shift+left, zooms with the wheel. Viewing only."
    )
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    handoff_description = "The embedded 3D viewer shows the model."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx)

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args["path"], want="file")
        suffix = path.suffix.lower()
        if suffix not in MODEL_SUFFIXES:
            hint = (
                " STEP files are not supported yet; a GLB or STL export of the part works."
                if suffix in (".step", ".stp")
                else ""
            )
            raise SkillError(f"{path.name}: the 3D viewer opens {', '.join(MODEL_SUFFIXES)}.{hint}")
        handoff = Handoff.model(path)
        ctx.services.ui.open_embedded(handoff)
        return SkillResult(
            f"Showing {path} ({human_size(path.stat().st_size)}) in the 3D viewer.", handoff
        )
