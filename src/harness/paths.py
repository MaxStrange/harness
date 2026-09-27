"""Locations of everything the harness keeps on disk.

Everything lives under one home directory (``~/.harness`` by default) so the
whole state of the harness can be backed up, inspected, or wiped in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HOME_ENV_VAR = "HARNESS_HOME"


def default_home() -> Path:
    """The harness home directory: ``$HARNESS_HOME`` if set, else ``~/.harness``."""
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".harness"


@dataclass(frozen=True)
class HarnessPaths:
    """Well-known paths, all derived from ``home``."""

    home: Path = field(default_factory=default_home)

    @property
    def config_file(self) -> Path:
        return self.home / "config.yml"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def sessions_db(self) -> Path:
        return self.home / "sessions.sqlite3"

    @property
    def user_skills_dir(self) -> Path:
        """Extra skills the user drops in without touching the package."""
        return self.home / "skills"

    @property
    def downloads_dir(self) -> Path:
        """Default untrusted location for anything that arrives from the web."""
        return self.home / "downloads"

    def ensure_directories(self) -> None:
        for path in (self.home, self.logs_dir, self.user_skills_dir, self.downloads_dir):
            path.mkdir(parents=True, exist_ok=True)
