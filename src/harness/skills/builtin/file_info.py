"""S8: size, dates and type of a file."""

from __future__ import annotations

import mimetypes
import stat as stat_module
from datetime import datetime

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import existing_path, human_size, looks_binary


class FileInfoSkill(Skill):
    name = "file_info"
    description = "Get size, timestamps, permissions and type of a file or directory."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    handoff_description = "The file is shown in the file manager."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx, "inspect")

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args["path"])
        st = path.stat()
        kind = "directory" if path.is_dir() else "symlink" if path.is_symlink() else "file"
        lines = [f"path: {path}", f"type: {kind}"]
        if path.is_file():
            mime, _ = mimetypes.guess_type(str(path))
            lines.append(f"size: {human_size(st.st_size)} ({st.st_size} bytes)")
            lines.append(f"mime type: {mime or 'unknown'}")
            lines.append(f"content: {'binary' if looks_binary(path) else 'text'}")
        else:
            try:
                lines.append(f"entries: {sum(1 for _ in path.iterdir())}")
            except OSError as exc:
                lines.append(f"entries: unreadable ({exc})")
        fmt = "%Y-%m-%d %H:%M:%S"
        lines.append(f"modified: {datetime.fromtimestamp(st.st_mtime).strftime(fmt)}")
        lines.append(f"changed: {datetime.fromtimestamp(st.st_ctime).strftime(fmt)}")
        lines.append(f"accessed: {datetime.fromtimestamp(st.st_atime).strftime(fmt)}")
        lines.append(f"permissions: {stat_module.filemode(st.st_mode)}")
        policy = ctx.services.path_policy
        if policy is not None and policy.is_untrusted(path):
            lines.append("trust: untrusted location (contents go through the web reader)")
        return SkillResult("\n".join(lines), Handoff.file_manager(path))
