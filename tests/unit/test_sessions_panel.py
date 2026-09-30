from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView  # noqa: E402

from harness.agent.store import SessionStore  # noqa: E402
from harness.model.types import Message  # noqa: E402
from harness.ui.sessions_panel import NO_PROJECT, SessionsPanel  # noqa: E402

# -- the store keeps a manual order ------------------------------------------------------


def titles(store, project_id="any"):
    return [
        s.title for s in store.list_sessions() if project_id == "any" or s.project_id == project_id
    ]


def test_new_sessions_go_on_top_and_reorder_sticks(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    a, b, c = (store.create_session("/tmp", t) for t in "abc")
    assert titles(store) == ["c", "b", "a"]  # newest on top
    store.reorder_sessions(None, [a.id, c.id, b.id])
    store.append_message(b.id, Message("user", "x"))
    assert titles(store) == ["a", "c", "b"]  # activity no longer reshuffles your order
    project = store.create_project("P")
    store.set_session_project(b.id, project.id)
    assert titles(store, project.id) == ["b"] and store.get_session(b.id).project_id == project.id
    store.close()


def test_projects_keep_their_order_and_new_ones_go_last(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    beta, alpha = store.create_project("beta"), store.create_project("alpha")
    assert [p.name for p in store.list_projects()] == ["beta", "alpha"]
    store.reorder_projects([alpha.id, beta.id])
    store.create_project("gamma")
    assert [p.name for p in store.list_projects()] == ["alpha", "beta", "gamma"]
    store.close()


def test_migration_keeps_the_order_people_saw(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, cwd TEXT NOT NULL, "
        "created REAL NOT NULL, updated REAL NOT NULL, tasks TEXT, project_id TEXT);"
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root_dir TEXT, "
        "instructions TEXT NOT NULL DEFAULT '', created REAL NOT NULL);"
        "INSERT INTO sessions VALUES ('1', 'older', '/', 1, 1, NULL, NULL);"
        "INSERT INTO sessions VALUES ('2', 'newer', '/', 2, 2, NULL, NULL);"
        "INSERT INTO projects VALUES ('p', 'zeta', NULL, '', 1);"
        "INSERT INTO projects VALUES ('q', 'Alpha', NULL, '', 2);"
    )
    conn.commit()
    conn.close()
    store = SessionStore(path)
    assert titles(store) == ["newer", "older"]
    assert [p.name for p in store.list_projects()] == ["Alpha", "zeta"]
    store.close()


# -- dragging in the panel ------------------------------------------------------------------


@pytest.fixture
def world(qtbot, tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    work, home = store.create_project("work"), store.create_project("home")
    ids = {}
    for title, project in [("w1", work), ("w2", work), ("w3", work), ("h1", home), ("x", None)]:
        ids[title] = store.create_session("/", title, project.id if project else None).id
    store.reorder_sessions(work.id, [ids["w1"], ids["w2"], ids["w3"]])
    panel = SessionsPanel(store)
    qtbot.addWidget(panel)
    panel.resize(300, 500)
    panel.show()
    yield panel, store, ids, work, home
    store.close()


def header(panel, name):
    for i in range(panel.tree.topLevelItemCount()):
        item = panel.tree.topLevelItem(i)
        if item.data(0, Qt.ItemDataRole.UserRole + 1) == name:
            return item
    raise AssertionError(name)


def item(panel, session_id):
    return panel._find(session_id)


def shown(panel):
    """The tree as text: project headers and their sessions, in order."""
    rows = []
    for i in range(panel.tree.topLevelItemCount()):
        top = panel.tree.topLevelItem(i)
        rows.append(top.data(0, Qt.ItemDataRole.UserRole + 1))
        rows += [
            "  " + top.child(j).data(0, Qt.ItemDataRole.UserRole + 1)
            for j in range(top.childCount())
        ]
    return rows


def test_reorder_within_a_project(world):
    panel, store, ids, work, _ = world
    assert shown(panel)[:4] == ["work", "  w1", "  w2", "  w3"]
    assert panel.drop(item(panel, ids["w3"]), item(panel, ids["w1"]), "above")
    assert shown(panel)[:4] == ["work", "  w3", "  w1", "  w2"]
    assert panel.drop(item(panel, ids["w3"]), item(panel, ids["w2"]), "below")  # down again
    assert shown(panel)[:4] == ["work", "  w1", "  w2", "  w3"]
    assert titles(store, work.id) == ["w1", "w2", "w3"]  # saved, not just shown


def test_drag_a_session_into_another_project(world):
    panel, store, ids, work, home = world
    moved = []
    panel.move_requested.connect(lambda sid, pid: moved.append((sid, pid)))
    panel.drop(item(panel, ids["w2"]), item(panel, ids["h1"]), "below")  # between sessions
    assert shown(panel) == ["work", "  w1", "  w3", "home", "  h1", "  w2", "No project", "  x"]
    assert moved == [(ids["w2"], home.id)] and store.get_session(ids["w2"]).project_id == home.id
    panel.drop(item(panel, ids["x"]), header(panel, "work"), "on")  # onto a header: its top
    assert shown(panel)[:2] == ["work", "  x"] and store.get_session(ids["x"]).project_id == work.id
    panel.drop(item(panel, ids["w1"]), header(panel, "No project"), "on")  # out of every project
    assert store.get_session(ids["w1"]).project_id is None
    # The gap above a header is the end of the project before it.
    panel.drop(item(panel, ids["h1"]), header(panel, "home"), "above")
    assert shown(panel)[shown(panel).index("home") - 1] == "  h1"


def test_reorder_projects_no_project_stays_last(world):
    panel, store, ids, work, home = world
    panel.drop(header(panel, "home"), header(panel, "work"), "above")
    assert [p.name for p in store.list_projects()] == ["home", "work"]
    assert [r for r in shown(panel) if not r.startswith("  ")] == ["home", "work", "No project"]
    assert panel.plan_drop(header(panel, "No project"), header(panel, "home"), "above") is None
    panel.drop(header(panel, "home"), header(panel, "No project"), "above")  # to the end
    assert [p.name for p in store.list_projects()] == ["work", "home"]
    panel.drop(header(panel, "work"), item(panel, ids["h1"]), "above")  # onto a session: after
    assert [p.name for p in store.list_projects()] == ["home", "work"]


def test_no_dragging_while_searching(world):
    panel, store, ids, work, _ = world
    panel.search.setText("w")
    assert not panel.tree.dragEnabled()
    assert panel.plan_drop(panel.tree.topLevelItem(0), None, "end") is None
    panel.search.setText("")
    assert panel.tree.dragEnabled()


def test_flat_list_without_projects(qtbot, tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    store.create_session("/", "a")
    b = store.create_session("/", "b")
    panel = SessionsPanel(store)
    qtbot.addWidget(panel)
    assert [panel.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole + 1) for i in range(2)] == [
        "b",
        "a",
    ]
    panel.drop(panel._find(b.id), None, "end")  # the empty space below: to the end
    assert titles(store) == ["a", "b"]
    store.close()


def test_a_real_drop_event_goes_through_the_panel(world, monkeypatch):
    panel, store, ids, work, home = world
    tree = panel.tree
    dragged = item(panel, ids["w3"])
    tree.setCurrentItem(dragged)
    target = item(panel, ids["w1"])
    point = tree.visualItemRect(target).center()
    monkeypatch.setattr(
        tree, "dropIndicatorPosition", lambda: QAbstractItemView.DropIndicatorPosition.AboveItem
    )
    event = QDropEvent(
        QPointF(point),
        Qt.DropAction.MoveAction,
        tree.mimeData([dragged]),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    tree.dropEvent(event)
    assert event.dropAction() == Qt.DropAction.IgnoreAction  # Qt did not move the item itself
    assert shown(panel)[:4] == ["work", "  w3", "  w1", "  w2"]
    assert tree.itemAt(QPoint(point)) is not None and NO_PROJECT in [
        tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
        for i in range(tree.topLevelItemCount())
    ]
