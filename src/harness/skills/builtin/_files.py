"""Shared helpers for the file skills (not a skill itself; underscore files are skipped)."""

from __future__ import annotations

from pathlib import Path

from harness.skills.base import SkillContext, SkillError, SkillResult

TEXT_SAMPLE = 8192


def existing_path(ctx: SkillContext, raw: str, *, want: str = "any") -> Path:
    path = ctx.resolve(raw)
    if not path.exists():
        raise SkillError(f"{path} does not exist")
    if want == "file" and not path.is_file():
        raise SkillError(f"{path} is not a file")
    if want == "dir" and not path.is_dir():
        raise SkillError(f"{path} is not a directory")
    return path


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(TEXT_SAMPLE)
    except OSError as exc:
        raise SkillError(f"cannot read {path}: {exc}") from exc
    return b"\x00" in sample


def read_text(path: Path, max_bytes: int) -> tuple[str, bool]:
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise SkillError(f"cannot read {path}: {exc}") from exc
    truncated = len(data) > max_bytes
    return data[:max_bytes].decode("utf-8", errors="replace"), truncated


def through_reader_if_untrusted(
    ctx: SkillContext, path: Path, content: str, question: str | None, handoff
) -> SkillResult | None:
    """SEC5: content under an untrusted location goes through the web reader, never straight to the model."""
    policy = ctx.services.path_policy
    if policy is None or not policy.is_untrusted(path):
        return None
    reader = ctx.services.web_reader
    if reader is None:
        return SkillResult.fail(
            f"{path} is in an untrusted location and the web reader is not available, so it cannot be read.",
            handoff,
        )
    result = reader.read(content, question, source=str(path), cancel=ctx.cancel)
    if not result.ok:
        return SkillResult.fail(f"{path} is in an untrusted location; {result.error}", handoff)
    note = (
        f"[{path} is in an untrusted location; this is the web reader's report, not the raw file]\n"
    )
    return SkillResult(note + (result.text or ""), handoff)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
