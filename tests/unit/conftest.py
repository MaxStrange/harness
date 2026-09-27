from __future__ import annotations

import pytest

from harness.config import Config
from harness.paths import HarnessPaths


@pytest.fixture
def harness_home(tmp_path, monkeypatch):
    """A throwaway ~/.harness so tests never touch the real one."""
    home = tmp_path / "harness_home"
    monkeypatch.setenv("HARNESS_HOME", str(home))
    paths = HarnessPaths(home=home)
    paths.ensure_directories()
    return paths


@pytest.fixture
def config(harness_home) -> Config:
    """A default config with every path pointed into the temporary home."""
    cfg = Config()
    cfg.logging.dir = str(harness_home.logs_dir)
    cfg.sessions.db_path = str(harness_home.sessions_db)
    cfg.web.untrusted_dirs = [str(harness_home.downloads_dir)]
    return cfg
