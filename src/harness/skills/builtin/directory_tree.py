"""Show a directory as a tree, a few levels deep, with big directories summarized."""

from __future__ import annotations

import os
from dataclasses import dataclass

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.skills.builtin._files import existing_path
from harness.skills.builtin.find_files import SKIP_DIRS

DEFAULT_DEPTH = 3
DEFAULT_MAX_ENTRIES = 40
HARD_ENTRY_LIMIT = 200
MAX_LINES = 2000


@dataclass
class Counts:
    files: int = 0
    dirs: int = 0

    def __str__(self) -> str:
        parts = []
        if self.dirs:
            parts.append(f"{self.dirs:,} dir{'s' if self.dirs != 1 else ''}")
        parts.append(f"{self.files:,} file{'s' if self.files != 1 else ''}")
        return ", ".join(parts)


def count_below(path: str, budget: int = 50000) -> tuple[Counts, bool]:
    """Files and directories under ``path``; stops (and says so) after ``budget`` entries."""
    counts = Counts()
    seen = 0
    for _root, dirs, files in os.walk(path, onerror=lambda _e: None):
        counts.dirs += len(dirs)
        counts.files += len(files)
        seen += len(dirs) + len(files)
        if seen >= budget:
            return counts, True
    return counts, False


class DirectoryTreeSkill(Skill):
    name = "directory_tree"
    description = (
        "Draw a directory as an indented tree, recursively to max_depth levels (default 3). "
        "Directories with more than max_entries entries show the first ones and a summary of the "
        "rest; directories below max_depth are shown with a count of what is inside. Version "
        "control and dependency folders (.git, node_modules, __pycache__, venvs) are summarized, "
        "not expanded, unless include_hidden is set."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to show; default is the session working directory.",
            },
            "max_depth": {
                "type": "integer",
                "description": f"Levels to descend (default {DEFAULT_DEPTH}, max 12).",
            },
            "max_entries": {
                "type": "integer",
                "description": f"Entries listed per directory before summarizing (default {DEFAULT_MAX_ENTRIES}, max {HARD_ENTRY_LIMIT}).",
            },
            "dirs_only": {"type": "boolean", "description": "Leave files out. Default false."},
            "include_hidden": {
                "type": "boolean",
                "description": "Show dot files and expand vendored folders. Default false.",
            },
        },
    }
    handoff_description = "The file explorer in the harness jumps to the directory."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args.get("path") or "."), args, ctx, "list")

    def run(self, args, ctx: SkillContext) -> SkillResult:
        root = existing_path(ctx, args.get("path") or ".", want="dir")
        max_depth = max(1, min(12, int(args.get("max_depth") or DEFAULT_DEPTH)))
        max_entries = max(
            1, min(HARD_ENTRY_LIMIT, int(args.get("max_entries") or DEFAULT_MAX_ENTRIES))
        )
        dirs_only = bool(args.get("dirs_only", False))
        include_hidden = bool(args.get("include_hidden", False))
        lines = [str(root) + os.sep]
        totals = Counts()
        truncated = False

        def walk(directory: str, prefix: str, depth: int) -> None:
            nonlocal truncated
            ctx.check_cancelled()
            if len(lines) >= MAX_LINES:
                truncated = True
                return
            try:
                with os.scandir(directory) as it:
                    entries = list(it)
            except OSError as exc:
                lines.append(f"{prefix}└── [unreadable: {exc.strerror or exc}]")
                return
            if not include_hidden:
                entries = [e for e in entries if not e.name.startswith(".")]
            dirs = sorted(
                (e for e in entries if e.is_dir(follow_symlinks=False)),
                key=lambda e: e.name.lower(),
            )
            files = (
                []
                if dirs_only
                else sorted(
                    (e for e in entries if not e.is_dir(follow_symlinks=False)),
                    key=lambda e: e.name.lower(),
                )
            )
            totals.dirs += len(dirs)
            totals.files += len(files)
            shown = (dirs + files)[:max_entries]
            hidden_rest = (dirs + files)[max_entries:]
            for i, entry in enumerate(shown):
                last = i == len(shown) - 1 and not hidden_rest
                branch = "└── " if last else "├── "
                child_prefix = prefix + ("    " if last else "│   ")
                if entry.is_dir(follow_symlinks=False):
                    skip = entry.name in SKIP_DIRS and not include_hidden
                    if depth >= max_depth or skip:
                        counts, partial = count_below(entry.path)
                        note = "not expanded" if skip else "below max_depth"
                        lines.append(
                            f"{prefix}{branch}{entry.name}/  ({counts}{'+' if partial else ''}, {note})"
                        )
                    else:
                        lines.append(f"{prefix}{branch}{entry.name}/")
                        walk(entry.path, child_prefix, depth + 1)
                else:
                    lines.append(f"{prefix}{branch}{entry.name}")
            if hidden_rest:
                rest = Counts(
                    files=sum(1 for e in hidden_rest if not e.is_dir(follow_symlinks=False)),
                    dirs=sum(1 for e in hidden_rest if e.is_dir(follow_symlinks=False)),
                )
                lines.append(f"{prefix}└── ... {len(hidden_rest):,} more ({rest})")

        walk(str(root), "", 1)
        summary = f"\n{totals} listed, depth {max_depth}"
        if truncated:
            summary += f"; output cut at {MAX_LINES} lines, narrow the path or lower max_depth"
        return SkillResult("\n".join(lines) + summary, Handoff.explorer(root))
