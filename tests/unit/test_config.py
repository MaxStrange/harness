from __future__ import annotations

import pytest

from harness.config import Config, ConfigError, default_config_text, load_config, parse_config


def test_default_file_matches_schema_defaults():
    """The comments in default_config.yml claim to show the defaults; keep that true."""
    from_file = parse_config(default_config_text(), "default_config.yml")
    assert from_file.model_dump() == Config().model_dump()


def test_empty_file_is_all_defaults():
    assert parse_config("", "empty").model_dump() == Config().model_dump()


def test_first_run_writes_default_config(harness_home):
    assert not harness_home.config_file.exists()
    cfg = load_config(harness_home)
    assert harness_home.config_file.exists()
    assert "models:" in harness_home.config_file.read_text()
    assert cfg.models.main.tool_format == "native"


def test_invalid_setting_is_named():
    text = "models:\n  main:\n    temperature: hot\n"
    with pytest.raises(ConfigError) as info:
        parse_config(text, "cfg")
    assert "models.main.temperature" in str(info.value)


def test_unknown_setting_is_named():
    with pytest.raises(ConfigError) as info:
        parse_config("web:\n  searxng: http://x\n", "cfg")
    assert "web.searxng" in str(info.value)
    assert "unknown setting" in str(info.value)


def test_bad_yaml_reports_source():
    with pytest.raises(ConfigError) as info:
        parse_config("models: [unclosed", "cfg")
    assert "cfg" in str(info.value)


def test_bad_url_named():
    with pytest.raises(ConfigError) as info:
        parse_config("models:\n  main:\n    endpoints:\n      - base_url: 10.0.0.1:8080\n", "c")
    assert "models.main.endpoints.0.base_url" in str(info.value)


def test_tilde_expanded_in_paths():
    cfg = parse_config("logging:\n  dir: ~/somewhere\n", "c")
    assert "~" not in cfg.logging.dir
    assert "~" not in Config().security.deny_paths[0]


def test_skill_timeout_lookup():
    cfg = Config()
    assert cfg.skills.timeout_for("terminal") == 300
    assert cfg.skills.timeout_for("nonexistent") == cfg.skills.default_timeout_s
