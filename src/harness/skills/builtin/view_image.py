"""S10: open an image in the embedded viewer."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path, human_size

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tif", ".tiff"}


class ViewImageSkill(Skill):
    name = "view_image"
    description = (
        "Show an image file to the user in the embedded image viewer and report its dimensions."
    )
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    handoff_description = "The embedded image viewer shows the image."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx)

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args["path"], want="file")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise SkillError(
                f"{path.name} does not have an image extension ({', '.join(sorted(IMAGE_SUFFIXES))})"
            )
        dims = _dimensions(path)
        handoff = Handoff.image(path)
        ctx.services.ui.open_embedded(handoff)
        info = f"{path} ({human_size(path.stat().st_size)})"
        if dims:
            info += f", {dims[0]}x{dims[1]} pixels"
        return SkillResult(f"Showing image in the embedded viewer: {info}", handoff)


def _dimensions(path) -> tuple[int, int] | None:
    try:
        from PySide6.QtGui import QImageReader

        size = QImageReader(str(path)).size()
        if size.isValid():
            return size.width(), size.height()
    except Exception:  # noqa: BLE001 - dimensions are a nicety
        return None
    return None
