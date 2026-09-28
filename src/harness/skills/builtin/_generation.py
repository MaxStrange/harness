"""Shared plumbing for the generation skills: the service, the stack, output files."""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import httpx

from harness.paths import HarnessPaths
from harness.skills.base import SkillContext, SkillError


def service_url(ctx: SkillContext, component: str) -> str:
    comp = ctx.config.models.stack.components.get(component)
    if comp is None or comp.kind != "service":
        raise SkillError(f"no generation service is configured for {component!r} (models.stack)")
    return comp.url


def make_room(ctx: SkillContext, component: str) -> None:
    """Load ``component``, unloading what it takes; the agent reloads it all afterwards."""
    stack = ctx.services.stack
    if stack is None:  # stack management off: the service loads lazily on the first job
        return
    from harness.model.stack import StackError

    try:
        stack.acquire([component], progress=ctx.say, cancel=ctx.cancel)
    except StackError as exc:
        raise SkillError(f"could not make room for {component}: {exc}") from exc


def call_service(ctx: SkillContext, component: str, path: str, payload: dict, timeout_s: float):
    """POST a job to the generation service and return its JSON."""
    ctx.check_cancelled()
    url = service_url(ctx, component) + path
    try:
        response = httpx.post(url, json=payload, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise SkillError(f"generation service {url}: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise SkillError(f"generation service {url}: HTTP {response.status_code}: {detail}")
    return response.json()


def output_path(ctx: SkillContext, requested: str | None, stem_hint: str, suffix: str) -> Path:
    """The file to write: the requested path (never overwritten) or a fresh one under generated/."""
    if requested:
        path = ctx.resolve(requested)
        if path.suffix.lower() != suffix:
            path = path.with_suffix(suffix)
        if path.exists():
            raise SkillError(f"{path} already exists; pick another name (nothing is overwritten)")
        return path
    stem = re.sub(r"[^a-z0-9]+", "-", stem_hint.lower()).strip("-")[:40] or "generated"
    folder = HarnessPaths().generated_dir
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}-{n}{suffix}"
        n += 1
    return path


def write_b64(path: Path, data: str) -> int:
    raw = base64.b64decode(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return len(raw)
