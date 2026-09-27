"""S4: read a file. Free for normal files, approval for protected paths, reader for untrusted ones."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import (
    existing_path,
    looks_binary,
    read_text,
    through_reader_if_untrusted,
)

MAX_BYTES = 400_000


class ReadFileSkill(Skill):
    name = "read_file"
    description = "Read a text file, optionally only a range of lines. For PDFs use pdf_read; for images use view_image."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-based first line to include."},
            "end_line": {"type": "integer", "description": "1-based last line to include."},
            "question": {
                "type": "string",
                "description": "If the file is in an untrusted location, the question the web reader should answer about it.",
            },
        },
        "required": ["path"],
    }
    handoff_description = "The file opens in the editor (VS Code by default)."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx)

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args["path"], want="file")
        handoff = Handoff.editor(path, args.get("start_line"))
        if looks_binary(path):
            raise SkillError(
                f"{path} looks like a binary file; use file_info, view_image or pdf_read"
            )
        text, truncated = read_text(path, MAX_BYTES)
        routed = through_reader_if_untrusted(ctx, path, text, args.get("question"), handoff)
        if routed is not None:
            return routed
        lines = text.splitlines()
        start = max(1, int(args.get("start_line") or 1))
        end = min(len(lines), int(args.get("end_line") or len(lines)))
        if start > len(lines):
            raise SkillError(f"{path} has only {len(lines)} lines")
        width = len(str(end))
        body = "\n".join(f"{i:>{width}}: {lines[i - 1]}" for i in range(start, end + 1))
        note = ""
        if truncated:
            note = f"\n[file truncated at {MAX_BYTES} bytes]"
        if start > 1 or end < len(lines):
            note += f"\n[showing lines {start}-{end} of {len(lines)}]"
        return SkillResult(body + note, handoff)
