"""S6: find files by name or glob pattern."""

from __future__ import annotations

import fnmatch
import os

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import existing_path

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
}


class FindFilesSkill(Skill):
    name = "find_files"
    description = "Find files and directories whose name matches a glob pattern (e.g. '*.py', 'README*'), recursively."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob on the file name, case-insensitive.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search; default is the session working directory.",
            },
            "max_results": {"type": "integer", "description": "Default 200."},
            "include_hidden": {
                "type": "boolean",
                "description": "Search inside dot directories and vendored folders like node_modules. Default false.",
            },
        },
        "required": ["pattern"],
    }
    handoff_description = "Any result opens in the editor or the file manager."

    def approval_request(self, args, ctx):
        return self.protected_path_request(
            ctx.resolve(args.get("path") or "."), args, ctx, "search"
        )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        root = existing_path(ctx, args.get("path") or ".", want="dir")
        pattern = args["pattern"].lower()
        limit = int(args.get("max_results") or 200)
        include_hidden = bool(args.get("include_hidden", False))
        matches: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            ctx.check_cancelled()
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            dirnames.sort()
            for name in sorted(dirnames + filenames):
                scanned += 1
                if fnmatch.fnmatch(name.lower(), pattern):
                    full = os.path.join(dirpath, name)
                    matches.append(full + ("/" if os.path.isdir(full) else ""))
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        header = f"{len(matches)} match(es) for {args['pattern']!r} under {root}" + (
            " (limit reached)" if len(matches) >= limit else ""
        )
        body = "\n".join(matches) if matches else "(none)"
        handoff = (
            Handoff.editor(matches[0].rstrip("/"))
            if matches and not matches[0].endswith("/")
            else Handoff.file_manager(matches[0].rstrip("/") if matches else root)
        )
        return SkillResult(f"{header}\n{body}", handoff, data={"matches": matches})
