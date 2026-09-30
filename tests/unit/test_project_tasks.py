from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402

from harness.agent.prompts import PROJECT_TASKS_SHOWN, build_system_prompt  # noqa: E402
from harness.agent.store import SessionStore  # noqa: E402
from harness.config import Config  # noqa: E402
from harness.model.types import ToolCall  # noqa: E402
from harness.skills.base import RecordingUi, Services, SkillContext  # noqa: E402
from harness.skills.registry import SkillRegistry  # noqa: E402
from harness.skills.runner import SkillRunner, auto_approve_broker  # noqa: E402
from harness.skills.tasklist_store import TaskListStore, project_key, session_key  # noqa: E402
from harness.ui.viewers.task_list_view import TaskListView  # noqa: E402


@pytest.fixture
def db(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    yield store
    store.close()


@pytest.fixture
def tasks(db):
    return TaskListStore(db.load_task_list, db.save_task_list)


# -- the store ----------------------------------------------------------------------------


def test_project_and_session_lists_are_separate_and_saved(db, tasks):
    project = db.create_project("P")
    session = db.create_session("/", "s", project.id)
    pkey, skey = project_key(project.id), session_key(session.id)
    tasks.add(pkey, "ship v1")
    tasks.add(skey, "fix the test")
    assert [t.text for t in tasks.get(pkey).tasks] == ["ship v1"]
    assert [t.text for t in tasks.get(skey).tasks] == ["fix the test"]
    fresh = TaskListStore(db.load_task_list, db.save_task_list)  # as after a restart
    assert [t.text for t in fresh.get(pkey).tasks] == ["ship v1"]
    assert [t.text for t in fresh.get(skey).tasks] == ["fix the test"]


def test_move_between_lists(db, tasks):
    project = db.create_project("P")
    session = db.create_session("/", "s", project.id)
    pkey, skey = project_key(project.id), session_key(session.id)
    a, b = tasks.add(pkey, "a"), tasks.add(pkey, "b")
    seen = []
    tasks.listeners.append(lambda key, _: seen.append(key))
    assert tasks.move(pkey, skey, a.id).text == "a"
    assert [t.text for t in tasks.get(pkey).tasks] == ["b"]
    assert [t.text for t in tasks.get(skey).tasks] == ["a"]
    assert seen == [pkey, skey]  # both panels hear about it
    tasks.move(pkey, skey, b.id, index=0)
    assert [t.text for t in tasks.get(skey).tasks] == ["b", "a"]
    assert tasks.move(pkey, skey, "nope") is None and tasks.move(skey, skey, a.id) is None


def test_old_database_gets_a_project_tasks_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root_dir TEXT, "
        "instructions TEXT NOT NULL DEFAULT '', created REAL NOT NULL);"
        "INSERT INTO projects VALUES ('p', 'P', NULL, '', 1);"
    )
    conn.commit()
    conn.close()
    store = SessionStore(path)
    assert store.load_task_list("project:p") is None
    store.save_task_list("project:p", '[{"id": "1", "text": "x", "status": "todo"}]')
    assert "x" in store.load_task_list("project:p")
    with pytest.raises(ValueError):
        store.load_task_list("elsewhere:p")
    store.close()


def test_damaged_list_starts_empty(db):
    session = db.create_session("/", "s")
    db.save_task_list(session_key(session.id), "{not json")
    tasks = TaskListStore(db.load_task_list, db.save_task_list)
    assert tasks.get(session_key(session.id)).tasks == []


# -- the skills ----------------------------------------------------------------------------


def run(tasks, name, project_id, **args):
    registry = SkillRegistry()
    registry.load_builtin()
    ui = RecordingUi()
    ctx = SkillContext(
        cwd=os.getcwd(),
        config=Config(),
        services=Services(ui=ui, task_lists=tasks),
        session_id="s1",
        project_id=project_id,
    )
    runner = SkillRunner(registry, auto_approve_broker())
    return runner.execute(ToolCall("c", name, args), ctx).result


def test_project_task_list_skill(tasks):
    out = run(tasks, "project_task_list", "p1", action="add", text="write the docs")
    assert out.ok and "write the docs" in out.content
    task_id = tasks.get("project:p1").tasks[0].id
    out = run(tasks, "project_task_list", "p1", action="update", task_id=task_id, status="doing")
    assert "[~]" in out.content
    # From the project to this session's checklist, and back again.
    out = run(tasks, "project_task_list", "p1", action="move", task_id=task_id)
    assert out.ok and "Moved" in out.content and tasks.get("project:p1").tasks == []
    assert tasks.get("session:s1").tasks[0].id == task_id
    out = run(tasks, "task_list", "p1", action="move", task_id=task_id, index=0)
    assert out.ok and tasks.get("project:p1").tasks[0].id == task_id
    assert "no task" in run(tasks, "project_task_list", "p1", action="remove", task_id="zz").content


def test_project_skill_needs_a_project(tasks):
    out = run(tasks, "project_task_list", None, action="show")
    assert not out.ok and "not in a project" in out.content
    run(tasks, "task_list", None, action="add", text="local")
    task_id = tasks.get("session:s1").tasks[0].id
    out = run(tasks, "task_list", None, action="move", task_id=task_id)
    assert not out.ok and "no project list" in out.content


def test_prompt_lists_open_project_tasks():
    prompt = build_system_prompt(
        "Hi.", "/", [], project_name="P", project_tasks=["a1: design (in progress)", "b2: build"]
    )
    assert "Open project tasks" in prompt and "- a1: design (in progress)" in prompt
    many = [f"t{i}: task {i}" for i in range(PROJECT_TASKS_SHOWN + 5)]
    prompt = build_system_prompt("Hi.", "/", [], project_name="P", project_tasks=many)
    assert f"t{PROJECT_TASKS_SHOWN - 1}:" in prompt and f"t{PROJECT_TASKS_SHOWN}:" not in prompt
    assert "+5 more" in prompt
    assert "Open project tasks" not in build_system_prompt("Hi.", "/", [], project_name="P")


def test_agent_gives_skills_the_project_and_the_prompt_its_tasks(tmp_path):
    from harness.agent.loop import Agent
    from harness.model.fake import FakeModel

    cfg = Config()
    cfg.sessions.db_path = str(tmp_path / "s.sqlite3")
    db = SessionStore(cfg.sessions.db_path)
    tasks = TaskListStore(db.load_task_list, db.save_task_list)
    project = db.create_project("P")
    tasks.add(project_key(project.id), "the backlog item")
    registry = SkillRegistry()
    registry.load_builtin()
    model = FakeModel(
        script=[FakeModel.tool_call("project_task_list", action="add", text="new idea"), "ok"]
    )
    services = Services(ui=RecordingUi(), task_lists=tasks)
    agent = Agent(cfg, model, registry, SkillRunner(registry, auto_approve_broker()), services, db)
    agent.new_session(tmp_path, project_id=project.id)
    assert "the backlog item" in agent.session.messages[0].content
    agent.send("remember an idea")
    texts = [t.text for t in tasks.get(project_key(project.id)).tasks]
    assert texts == ["the backlog item", "new idea"]
    db.close()


# -- the panel ------------------------------------------------------------------------------


@pytest.fixture
def view(qtbot, db, tasks):
    project = db.create_project("Harness")
    session = db.create_session("/", "s", project.id)
    widget = TaskListView(tasks)
    tasks.listeners.append(widget.on_store_changed)
    qtbot.addWidget(widget)
    widget.show()
    widget.set_session(session.id, project.id, project.name)
    return widget, tasks, project_key(project.id), session_key(session.id)


def texts(widget):
    return [widget.item(i).text() for i in range(widget.count())]


def test_panel_shows_both_lists(view):
    widget, tasks, pkey, skey = view
    tasks.add(pkey, "project thing")
    tasks.add(skey, "session thing")
    assert widget.project_list.isVisible() and widget.project_title.text() == "Project: Harness"
    assert texts(widget.project_list) == ["project thing"]
    assert texts(widget.list) == ["session thing"]
    widget.set_session("loose")  # a session with no project: only its own list
    assert not widget.project_list.isVisible() and texts(widget.list) == []


def test_panel_drag_between_lists_and_tick_saves(view, db):
    widget, tasks, pkey, skey = view
    a, b = tasks.add(pkey, "a"), tasks.add(skey, "b")
    widget.list.dropped.emit(widget.project_list, a.id, 0)  # project -> session, on top
    assert texts(widget.list) == ["a", "b"] and texts(widget.project_list) == []
    widget.project_list.dropped.emit(widget.list, b.id, 0)  # session -> project
    assert texts(widget.project_list) == ["b"]
    widget.project_list.item(0).setCheckState(Qt.CheckState.Checked)  # the user ticks it
    assert tasks.get(pkey).tasks[0].status == "done"
    assert '"done"' in db.load_task_list(pkey)  # saved straight away
    assert "drag to reorder or to the session list" in widget.project_summary.text()
