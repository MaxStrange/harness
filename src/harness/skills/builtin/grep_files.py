"""S7: search file contents."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path
from harness.skills.builtin.find_files import SKIP_DIRS

MAX_FILE_BYTES = 5_000_000


class GrepFilesSkill(Skill):
    name = "grep_files"
    description = "Search for a regular expression inside text files, recursively. Returns file:line: text matches."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression."},
            "path": {
                "type": "string",
                "description": "File or directory to search; default is the session working directory.",
            },
            "file_glob": {
                "type": "string",
                "description": "Only search files whose name matches this glob, e.g. '*.py'.",
            },
            "case_sensitive": {"type": "boolean", "description": "Default false."},
            "max_results": {"type": "integer", "description": "Default 200."},
            "context_lines": {
                "type": "integer",
                "description": "Lines of context around each match. Default 0.",
            },
        },
        "required": ["pattern"],
    }
    handoff_description = "Any match opens in the editor at that line."

    def approval_request(self, args, ctx):
        return self.protected_path_request(
            ctx.resolve(args.get("path") or "."), args, ctx, "search"
        )

    def run(self, args, ctx: SkillContext) -> SkillResult:
        root = existing_path(ctx, args.get("path") or ".")
        flags = 0 if args.get("case_sensitive") else re.IGNORECASE
        try:
            regex = re.compile(args["pattern"], flags)
        except re.error as exc:
            raise SkillError(f"invalid regular expression: {exc}") from exc
        limit = int(args.get("max_results") or 200)
        context = int(args.get("context_lines") or 0)
        file_glob = args.get("file_glob")
        policy = ctx.services.path_policy
        results: list[str] = []
        first: tuple[str, int] | None = None
        untrusted_skipped = 0

        def search_file(path: str) -> bool:
            nonlocal first, untrusted_skipped
            if policy is not None and policy.is_untrusted(Path(path)):
                untrusted_skipped += 1
                return False
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    return False
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                return False
            if b"\x00" in data[:8192]:
                return False
            lines = data.decode("utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines):
                if regex.search(line):
                    if first is None:
                        first = (path, index + 1)
                    if context:
                        lo, hi = max(0, index - context), min(len(lines), index + context + 1)
                        block = "\n".join(
                            f"{path}:{i + 1}{':' if i == index else '-'} {lines[i]}"
                            for i in range(lo, hi)
                        )
                        results.append(block + "\n--")
                    else:
                        results.append(f"{path}:{index + 1}: {line.strip()[:400]}")
                    if len(results) >= limit:
                        return True
            return False

        if root.is_file():
            search_file(str(root))
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                ctx.check_cancelled()
                dirnames[:] = sorted(
                    d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                )
                for name in sorted(filenames):
                    if file_glob and not fnmatch.fnmatch(name, file_glob):
                        continue
                    if search_file(os.path.join(dirpath, name)):
                        break
                if len(results) >= limit:
                    break
        header = f"{len(results)} match(es) for /{args['pattern']}/ under {root}" + (
            " (limit reached)" if len(results) >= limit else ""
        )
        if untrusted_skipped:
            header += f"; skipped {untrusted_skipped} file(s) in untrusted locations (read them with read_file to go through the web reader)"
        handoff = (
            Handoff.editor(first[0], first[1])
            if first
            else Handoff.file_manager(root if root.is_dir() else root.parent)
        )
        return SkillResult(header + "\n" + ("\n".join(results) if results else "(none)"), handoff)
