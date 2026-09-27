from __future__ import annotations

from pathlib import Path

import pytest

from harness.agent.loop import Agent
from harness.agent.prompts import build_system_prompt
from harness.agent.store import SessionStore
from harness.config import Config
from harness.model.fake import FakeModel
from harness.skills.base import RecordingUi, Services
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker


def test_store_projects(tmp_path):
    store = SessionStore(tmp_path / "db.sqlite3")
    thesis = store.create_project("Thesis", str(tmp_path), "Cite with BibTeX.")
    assert store.get_project(thesis.id).instructions == "Cite with BibTeX."
    s1 = store.create_session("/tmp", "one", project_id=thesis.id)
    s2 = store.create_session("/tmp", "two")
    assert store.get_session(s1.id).project_id == thesis.id
    assert store.get_session(s2.id).project_id is None
    store.set_session_project(s2.id, thesis.id)
    assert store.get_session(s2.id).project_id == thesis.id
    store.update_project(thesis.id, name="PhD", root_dir="", instructions="x")
    project = store.get_project(thesis.id)
    assert (project.name, project.root_dir, project.instructions) == ("PhD", None, "x")
    assert [p.name for p in store.list_projects()] == ["PhD"]
    store.delete_project(thesis.id)
    assert store.list_projects() == [] and store.get_session(s1.id).project_id is None
    store.close()


def test_store_migrates_old_schema(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, cwd TEXT NOT NULL, "
        "created REAL NOT NULL, updated REAL NOT NULL, tasks TEXT);"
        "INSERT INTO sessions VALUES ('abc', 'old', '/tmp', 1, 1, NULL);"
    )
    conn.commit()
    conn.close()
    store = SessionStore(path)
    assert store.get_session("abc").project_id is None
    store.close()


def test_prompt_includes_global_and_project_context(tmp_path):
    reg = SkillRegistry()
    reg.load_builtin()
    prompt = build_system_prompt(
        "Be nice.",
        tmp_path,
        reg.all(),
        global_context="Papers live in ~/research.",
        project_name="Thesis",
        project_root="/home/me/thesis",
        project_instructions="Use scratch/ for temp files.",
    )
    assert "Standing facts" in prompt and "Papers live in ~/research." in prompt
    assert "## Project: Thesis" in prompt and "/home/me/thesis" in prompt and "scratch/" in prompt
    plain = build_system_prompt("Be nice.", tmp_path, reg.all())
    assert "Standing facts" not in plain and "## Project" not in plain


def test_agent_uses_project_root_and_context_file(tmp_path, harness_home):
    cfg = Config()
    cfg.sessions.db_path = str(tmp_path / "s.sqlite3")
    registry = SkillRegistry()
    registry.load_builtin()
    store = SessionStore(cfg.sessions.db_path)
    agent = Agent(
        cfg,
        FakeModel(script=["ok"]),
        registry,
        SkillRunner(registry, auto_approve_broker()),
        Services(ui=RecordingUi()),
        store,
        paths=harness_home,
    )
    harness_home.global_context_file.write_text("Scratch work goes in /tmp/scratch.\n")
    root = tmp_path / "proj"
    root.mkdir()
    project = store.create_project("Proj", str(root), "Always run the tests.")
    session = agent.new_session(project_id=project.id)
    assert session.cwd == Path(root) and session.project_id == project.id
    system = session.messages[0].content
    assert (
        "/tmp/scratch" in system
        and "## Project: Proj" in system
        and "Always run the tests." in system
    )
    agent.set_project(None)
    assert "## Project" not in session.messages[0].content
    assert store.get_session(session.id).project_id is None
    store.close()


def test_advanced_search_filters(tmp_path):
    import time

    store = SessionStore(tmp_path / "db.sqlite3")
    thesis = store.create_project("Thesis", None, "")
    a = store.create_session("/tmp", "in project", project_id=thesis.id)
    b = store.create_session("/tmp", "loose")
    from harness.model.types import Message

    store.append_message(a.id, Message("user", "Lizard green looks Great"))
    store.append_message(a.id, Message("assistant", "Agreed about the lizard."))
    store.append_message(b.id, Message("user", "lizard tail question"))
    from harness.agent.store import ANY_PROJECT

    assert {h.session_id for h in store.search("lizard")} == {a.id, b.id}
    assert {h.session_id for h in store.search("lizard", project_id=thesis.id)} == {a.id}
    assert {h.session_id for h in store.search("lizard", project_id=None)} == {b.id}
    assert {h.role for h in store.search("lizard", role="assistant")} == {"assistant"}
    assert store.search("Great", case_sensitive=True) and not store.search(
        "great", case_sensitive=True
    )
    hits = store.search(r"liz\w+ (green|tail)", regex=True)
    assert (
        {h.session_id for h in hits} == {a.id, b.id}
        and "[Lizard green]" in hits[0].snippet
        or "[lizard tail]" in hits[0].snippet
    )
    with pytest.raises(ValueError):
        store.search("(unclosed", regex=True)
    assert store.search("lizard", since=time.time() + 10) == []
    assert store.search("lizard", project_id=ANY_PROJECT, since=time.time() - 10)
    store.close()
