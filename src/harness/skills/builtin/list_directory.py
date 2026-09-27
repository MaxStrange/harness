"""S5: list a directory."""

from __future__ import annotations

from datetime import datetime

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import existing_path, human_size


class ListDirectorySkill(Skill):
    name = "list_directory"
    description = "List the entries of a directory with sizes and modification times."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list; default is the session working directory.",
            },
            "show_hidden": {"type": "boolean", "description": "Include dot files. Default false."},
        },
    }
    handoff_description = "The directory opens in the system file manager."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args.get("path") or "."), args, ctx, "list")

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args.get("path") or ".", want="dir")
        show_hidden = bool(args.get("show_hidden", False))
        lines = [f"{path}:"]
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        count = 0
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                when = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                size = "<dir>" if entry.is_dir() else human_size(stat.st_size)
            except OSError:
                when, size = "?", "?"
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{when}  {size:>9}  {entry.name}{suffix}")
            count += 1
            if count >= 500:
                lines.append("... (more than 500 entries; use find_files to narrow down)")
                break
        if count == 0:
            lines.append("(empty)")
        untrusted = ctx.services.path_policy is not None and ctx.services.path_policy.is_untrusted(
            path
        )
        if untrusted:
            lines.append(
                "[note: this directory is an untrusted location; file contents go through the web reader]"
            )
        return SkillResult("\n".join(lines), Handoff.file_manager(path))
