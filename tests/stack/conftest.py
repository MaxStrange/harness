"""Stack tests (P17) run against the user's real ~/.harness/config.yml.

Run them explicitly: ``pytest tests/stack -v``. They are skipped when no config exists.
"""

from __future__ import annotations

import pytest

from harness.config import ConfigError, load_config
from harness.paths import HarnessPaths


@pytest.fixture(scope="session")
def stack_config():
    paths = HarnessPaths()
    if not paths.config_file.exists():
        pytest.skip(f"no config at {paths.config_file}; launch the harness once to create it")
    try:
        return load_config(paths)
    except ConfigError as exc:
        pytest.fail(str(exc))


@pytest.fixture(scope="session")
def models(stack_config):
    from harness.bootstrap import build_models

    return build_models(stack_config)
