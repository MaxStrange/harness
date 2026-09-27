"""S3: read a PDF's text."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillError, SkillResult
from harness.skills.builtin._files import existing_path, through_reader_if_untrusted

MAX_CHARS = 200_000


class PdfReadSkill(Skill):
    name = "pdf_read"
    description = "Extract the text of a PDF file (optionally a page range) so you can read it."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "first_page": {"type": "integer", "description": "1-based first page (default 1)."},
            "last_page": {"type": "integer", "description": "1-based last page (default: last)."},
            "question": {
                "type": "string",
                "description": "If the PDF is in an untrusted location, the question the web reader should answer about it.",
            },
        },
        "required": ["path"],
    }
    handoff_description = "The PDF opens in the system's default PDF viewer (usually the browser)."

    def approval_request(self, args, ctx):
        return self.protected_path_request(ctx.resolve(args["path"]), args, ctx)

    def run(self, args, ctx: SkillContext) -> SkillResult:
        path = existing_path(ctx, args["path"], want="file")
        handoff = Handoff.default_app(path, label="Open PDF")
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise SkillError("pypdf is not installed") from exc
        try:
            reader = PdfReader(str(path))
            total = len(reader.pages)
        except Exception as exc:  # noqa: BLE001 - pypdf raises many types
            raise SkillError(f"cannot open {path} as a PDF: {exc}") from exc
        first = max(1, int(args.get("first_page") or 1))
        last = min(total, int(args.get("last_page") or total))
        if first > total:
            raise SkillError(f"{path} has only {total} page(s)")
        parts = []
        size = 0
        for number in range(first, last + 1):
            ctx.check_cancelled()
            try:
                text = reader.pages[number - 1].extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                text = f"[could not extract page {number}: {exc}]"
            parts.append(f"--- page {number} ---\n{text.strip()}")
            size += len(text)
            if size > MAX_CHARS:
                parts.append(
                    f"[stopped after page {number}: output over {MAX_CHARS} characters; request a smaller page range]"
                )
                break
        content = f"{path} ({total} pages, showing {first}-{min(last, number)})\n" + "\n".join(
            parts
        )
        routed = through_reader_if_untrusted(ctx, path, content, args.get("question"), handoff)
        if routed is not None:
            return routed
        return SkillResult(content, handoff)
