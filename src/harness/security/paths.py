"""Path policy (P9, SEC5): what the model may read freely, with approval, or only via the reader."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from harness.config import SecurityConfig, WebConfig


def resolve_path(path: str, cwd: str | Path) -> Path:
    """Expand ``~`` and resolve ``path`` against the session's working directory (P12)."""
    expanded = Path(os.path.expanduser(path))
    if not expanded.is_absolute():
        expanded = Path(cwd) / expanded
    try:
        return expanded.resolve()
    except OSError:
        return expanded.absolute()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class PathPolicy:
    deny_paths: list[Path] = field(default_factory=list)
    deny_names: set[str] = field(default_factory=set)
    untrusted_dirs: list[Path] = field(default_factory=list)

    @classmethod
    def from_config(cls, security: SecurityConfig, web: WebConfig) -> PathPolicy:
        # Entries that do not exist on this machine are simply ignored (P9).
        deny = []
        for raw in security.deny_paths:
            candidate = Path(raw).expanduser()
            if candidate.exists():
                deny.append(candidate.resolve())
        untrusted = [Path(d).expanduser().resolve() for d in web.untrusted_dirs]
        return cls(deny_paths=deny, deny_names=set(security.deny_names), untrusted_dirs=untrusted)

    def denial_reason(self, path: Path) -> str | None:
        """Why ``path`` needs explicit approval, or None if it is free to read."""
        resolved = path if path.is_absolute() else path.resolve()
        for root in self.deny_paths:
            if resolved == root or _is_within(resolved, root):
                return f"{resolved} is under the protected location {root}"
        for part in resolved.parts:
            if part in self.deny_names:
                return f"{resolved} matches the protected file name {part!r}"
        return None

    def is_untrusted(self, path: Path) -> bool:
        """True when content at ``path`` arrived from the web and must go through the reader."""
        resolved = path if path.is_absolute() else path.resolve()
        return any(resolved == root or _is_within(resolved, root) for root in self.untrusted_dirs)
