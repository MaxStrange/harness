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


def test_api_key_file_is_read_and_expanded(tmp_path):
    from harness.config import Endpoint

    key_file = tmp_path / "key"
    key_file.write_text("sk-test-123\n", encoding="utf-8")
    endpoint = Endpoint(base_url="http://x/v1", api_key_file=str(key_file))
    assert endpoint.resolve_api_key() == "sk-test-123"
    assert Endpoint(base_url="http://x/v1", api_key="inline").resolve_api_key() == "inline"
    assert Endpoint(base_url="http://x/v1").resolve_api_key() is None


def test_api_key_file_problems_are_reported(tmp_path):
    from harness.config import Endpoint

    missing = Endpoint(base_url="http://x/v1", api_key_file=str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="cannot be read"):
        missing.resolve_api_key()
    (tmp_path / "empty").write_text("\n", encoding="utf-8")
    empty = Endpoint(base_url="http://x/v1", api_key_file=str(tmp_path / "empty"))
    with pytest.raises(ConfigError, match="is empty"):
        empty.resolve_api_key()


def test_api_key_and_api_key_file_are_exclusive():
    text = (
        "models:\n  main:\n    endpoints:\n"
        "      - base_url: http://x/v1\n        api_key: a\n        api_key_file: /k\n"
    )
    with pytest.raises(ConfigError, match="not both"):
        parse_config(text, "cfg")


def test_reset_sessions_cli(harness_home, monkeypatch, capsys):
    from harness.agent.store import SessionStore
    from harness.cli import main

    load_config(harness_home)
    store = SessionStore(harness_home.sessions_db)
    store.create_session("/tmp", "old")
    store.close()
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert main(["--reset-sessions"]) == 1
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert main(["--reset-sessions"]) == 0
    assert "deleted 1 session" in capsys.readouterr().out
    assert SessionStore(harness_home.sessions_db).list_sessions() == []


def test_default_config_follows_harness_home(harness_home):
    text = default_config_text()
    assert str(harness_home.home) in text and "~/.harness" not in text
    cfg = parse_config(text, "default")
    assert cfg.sessions.db_path == str(harness_home.sessions_db)


def test_set_config_value_keeps_comments(tmp_path):
    from harness.config import set_config_value

    path = tmp_path / "config.yml"
    path.write_text(
        "# top\nui:\n  window_title: AI Harness\n  critter: lizard   # sprite\n\nweb:\n  searxng_url: http://x\n"
    )
    set_config_value(path, "ui.critter", "turtle")
    text = path.read_text()
    assert "  critter: turtle  # sprite" in text and "# top" in text and "searxng_url" in text
    set_config_value(path, "ui.font_size", "12")
    assert "  font_size: '12'" in path.read_text() or "  font_size: 12" in path.read_text()
    set_config_value(path, "handoff.editor", "vim {path}")
    assert "handoff:\n  editor: vim {path}" in path.read_text()
    assert parse_config(path.read_text(), "c").ui.critter == "turtle"
