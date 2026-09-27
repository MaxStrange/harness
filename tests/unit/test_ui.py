"""GUI tests run offscreen with pytest-qt. They exercise wiring and logic, not looks."""

from __future__ import annotations

import os
import shutil
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

from harness.agent.loop import Agent  # noqa: E402
from harness.bootstrap import build_core  # noqa: E402
from harness.config import Config  # noqa: E402
from harness.model.fake import FakeModel  # noqa: E402
from harness.skills.base import ApprovalDecision, ApprovalRequest, Handoff  # noqa: E402
from harness.skills.runner import PendingApproval  # noqa: E402
from harness.ui.bridge import AgentController, MainThreadInvoker, QtUiBridge  # noqa: E402
from harness.ui.chat.approval_widget import ApprovalWidget  # noqa: E402
from harness.ui.dock import Dock  # noqa: E402
from harness.ui.main_window import MainWindow  # noqa: E402
from harness.ui.markdown import MarkdownRenderer, render_diff  # noqa: E402
from harness.ui.theme import build_stylesheet  # noqa: E402

bash_only = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"), reason="needs bash"
)


@pytest.fixture
def window(qtbot, harness_home, tmp_path):
    cfg = Config()
    cfg.sessions.db_path = str(harness_home.sessions_db)
    cfg.logging.dir = str(harness_home.logs_dir)
    cfg.sessions.default_cwd = str(tmp_path)
    cfg.ui.file_explorer_root = str(tmp_path)
    cfg.web.untrusted_dirs = []
    cfg.security.deny_paths = []
    holder = {}
    bridge = QtUiBridge(lambda h: holder["w"].handoffs.open_external(h))
    core = build_core(cfg, harness_home, ui=bridge)
    model = FakeModel()
    core.main_model = model  # the fake replaces the network client
    agent = Agent(cfg, model, core.registry, core.runner, core.services, core.store)
    controller = AgentController(agent, core.broker)
    win = MainWindow(core, agent, controller, bridge)
    holder["w"] = win
    qtbot.addWidget(win)
    win.new_session()
    yield win, model, core
    win.close()
    core.shutdown()


def wait_turn(qtbot, window, timeout=10000):
    with qtbot.waitSignal(window.controller.signals.turn_finished, timeout=timeout):
        pass


def test_stylesheet_and_markdown_render():
    cfg = Config()
    qss = build_stylesheet(cfg.ui)
    assert cfg.ui.theme.accent in qss and "QPushButton#accent" in qss
    html = MarkdownRenderer(cfg.ui.theme).render(
        "# Title\n\nSome `code` and\n\n```python\nprint('x')\n```\n"
    )
    assert "<h1>" in html and "print" in html and "font-family:monospace" in html
    diff = render_diff("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n", cfg.ui.theme)
    assert cfg.ui.theme.accent in diff and cfg.ui.theme.error in diff


def test_window_streams_reply_and_lists_session(qtbot, window):
    win, model, core = window
    model.script.append("Hello **there**.")
    win.composer.input.setPlainText("hi")
    win.composer._submit()
    wait_turn(qtbot, win)
    from PySide6.QtWidgets import QLabel

    texts = " ".join(label.text() for label in win.chat._container.findChildren(QLabel))
    assert "there" in texts
    assert not win.controller.busy
    assert core.store.get_session(win.agent.session.id).title == "hi"
    assert win.sessions.list.count() == 1


def test_window_skill_call_shows_bubble_and_handoff_button(qtbot, window, tmp_path):
    win, model, core = window
    (tmp_path / "f.txt").write_text("content\n")
    model.script += [FakeModel.tool_call("read_file", path="f.txt"), "It says content."]
    win.send_message("read f.txt")
    wait_turn(qtbot, win)
    from harness.ui.chat.message_widget import SkillBubble

    bubbles = win.chat._container.findChildren(SkillBubble)
    assert len(bubbles) == 1
    assert bubbles[0].handoff is not None and bubbles[0].handoff.action == "editor"
    assert bubbles[0].handoff_button.text() == "Open in editor"
    assert "done" in bubbles[0].text_label.text()


def test_window_approval_flow_approve_via_widget(qtbot, window, tmp_path):
    win, model, core = window
    model.script += [
        FakeModel.tool_call("write_file", action="write", path="new.txt", content="hi\n"),
        "Written.",
    ]
    win.send_message("write it")
    with qtbot.waitSignal(win.controller.signals.approval_needed, timeout=10000):
        pass
    qtbot.waitUntil(lambda: bool(win.chat._container.findChildren(ApprovalWidget)), timeout=5000)
    widget = win.chat._container.findChildren(ApprovalWidget)[0]
    assert widget.pending.request.detail_kind == "diff" and "+hi" in widget.pending.request.detail
    widget.approve_button.click()
    wait_turn(qtbot, win)
    assert (tmp_path / "new.txt").read_text() == "hi\n"
    assert widget.decided and "Approved" in widget.outcome_label.text()


def test_window_approval_reject_with_reason(qtbot, window, tmp_path):
    win, model, core = window
    model.script += [FakeModel.tool_call("terminal", command="rm -rf /"), "Okay, not running it."]
    core.summarizer.model = FakeModel(offline_reason="down")
    win.send_message("wipe")
    qtbot.waitUntil(lambda: bool(win.chat._container.findChildren(ApprovalWidget)), timeout=10000)
    widget = win.chat._container.findChildren(ApprovalWidget)[0]
    qtbot.waitUntil(lambda: "offline" in widget.summary_label.text(), timeout=5000)  # SEC1a
    widget.reason_edit.setText("dangerous")
    widget.reject_button.click()
    wait_turn(qtbot, win)
    tool_msg = model.requests[1][-1]
    assert "rejected" in tool_msg.content and "dangerous" in tool_msg.content


def test_approval_widget_edit_then_approve(qtbot):
    cfg = Config()
    decisions = []
    pending = PendingApproval(
        "id1",
        ApprovalRequest(
            "terminal", "Run?", "ls", "command", editable_field="command", summary="Lists files."
        ),
    )
    widget = ApprovalWidget(pending, cfg.ui.theme, lambda i, d: decisions.append((i, d)) or True)
    qtbot.addWidget(widget)
    widget.edit_button.click()  # first click reveals the editor
    assert widget.editor.isVisibleTo(widget)
    widget.editor.setPlainText("ls -la")
    widget.edit_button.click()
    assert decisions == [("id1", ApprovalDecision(True, {"command": "ls -la"}, None))]
    widget.approve_button.click()  # second decision ignored
    assert len(decisions) == 1


def test_stop_cancels_turn(qtbot, window):
    win, model, core = window
    model.chunk_size = 1
    model.script.append("x" * 5000)
    original = model.stream_chat

    def slow(*args, **kwargs):
        import time

        for event in original(*args, **kwargs):
            time.sleep(0.001)
            yield event

    model.stream_chat = slow
    win.send_message("go")
    qtbot.waitUntil(lambda: win.composer.stop_button.isVisibleTo(win), timeout=5000)
    win.controller.stop()
    wait_turn(qtbot, win)
    assert not win.controller.busy


def test_session_switching_and_search(qtbot, window):
    win, model, core = window
    model.script += ["about lizards", "about pandas"]
    first = win.agent.session.id
    win.send_message("tell me about lizards")
    wait_turn(qtbot, win)
    win.new_session()
    second = win.agent.session.id
    win.send_message("tell me about pandas")
    wait_turn(qtbot, win)
    assert first != second and win.sessions.list.count() == 2
    win.sessions.search.setText("lizards")
    assert win.sessions.list.count() == 1 and win.sessions.list.item(0).data(0x0100) == first
    win.open_session(first)
    assert win.agent.session.id == first
    from harness.ui.chat.message_widget import AssistantBubble, UserBubble

    assert len(win.chat._container.findChildren(UserBubble)) == 1
    assert "lizards" in win.chat._container.findChildren(AssistantBubble)[0].text


def test_dock_is_centred_and_magnifies_along_an_arc(qtbot):
    from PySide6.QtCore import QPointF

    dock = Dock(Config().ui.theme)
    qtbot.addWidget(dock)
    dock.resize(64, 400)
    for name in ("terminal", "image", "tasks"):
        dock.add_item(name, name, "x")
    resting = dock._rects()
    # Vertically centred: the strip's middle is the widget's middle.
    middle = (resting[0].top() + resting[-1].bottom()) / 2
    assert middle == pytest.approx(200, abs=1)
    assert all(r.width() == 36 for r in resting)
    # Hover the first icon: it grows, slides right (the arc), and the far icon stays put.
    dock._mouse_y = resting[0].center().y()
    dock._target_intensity = 1.0
    dock.settle()
    magnified = dock._rects()
    assert magnified[0].width() > resting[0].width()
    assert magnified[0].center().x() > resting[0].center().x()
    assert magnified[2].width() == pytest.approx(resting[2].width(), abs=1)
    assert magnified[0].right() <= dock.width()
    assert dock.item_at(QPointF(magnified[0].center())).name == "terminal"
    clicked = []
    dock.item_clicked.connect(clicked.append)
    # Leaving eases back to rest.
    dock.leaveEvent(None)
    dock.settle()
    assert dock._rects()[0].width() == pytest.approx(36)


def test_sessions_panel_click_keeps_items_and_rename_delegate(qtbot, window):
    win, model, core = window
    model.script += ["one", "two"]
    win.send_message("first session")
    wait_turn(qtbot, win)
    first = win.agent.session.id
    win.new_session()
    second = win.agent.session.id
    panel = win.sessions
    assert panel.list.currentItem().data(0x0100) == second
    item = panel._find(first)
    panel.list.itemClicked.emit(item)  # a click on the other session opens it ...
    assert win.agent.session.id == first
    assert panel._find(first) is item  # ... without rebuilding the list
    assert panel.list.currentItem() is item
    # Inline rename through the delegate.
    from PySide6.QtWidgets import QLineEdit

    delegate = panel.list.itemDelegate()
    index = panel.list.indexFromItem(item)
    editor = delegate.createEditor(panel.list, None, index)
    delegate.setEditorData(editor, index)
    assert isinstance(editor, QLineEdit) and editor.text() == "first session"
    editor.setText("Renamed")
    delegate.setModelData(editor, panel.list.model(), index)
    assert core.store.get_session(first).title == "Renamed"
    assert panel._find(first).data(0x0101) == "Renamed"
    assert panel.list.currentItem().data(0x0100) == first


def test_set_cwd_moves_explorer(qtbot, window, tmp_path):
    win, model, core = window
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    win.set_cwd(str(sub))
    assert win.explorer.model.rootPath() == str(sub)
    assert win.cwd_label.text() == str(sub)


def test_views_toggle_and_image_viewer(qtbot, window, tmp_path):
    win, model, core = window
    from PySide6.QtGui import QImage

    img = tmp_path / "p.png"
    QImage(10, 5, QImage.Format.Format_RGB32).save(str(img))
    win.perform_handoff(Handoff.image(img))
    assert win.side_panel.isVisibleTo(win) and win.dock.active == "image"
    assert "10x5" in win.image_viewer.title.text()
    win.toggle_view("image")
    assert not win.side_panel.isVisibleTo(win) and win.dock.active is None
    win.perform_handoff(Handoff.tasks())
    assert win.dock.active == "tasks"
    core.task_lists.add(win.agent.session.id, "do a thing")
    qtbot.waitUntil(lambda: win.task_view.list.count() == 1, timeout=3000)
    assert "do a thing" in win.task_view.list.item(0).text()


def test_ui_bridge_from_worker_thread(qtbot, window):
    import threading

    win, model, core = window
    results = {}

    def worker():
        results["clip"] = core.services.ui.set_clipboard("copied")
        results["err"] = core.services.ui.open_external(Handoff("external", "bogus", "x"))

    thread = threading.Thread(target=worker)
    thread.start()
    qtbot.waitUntil(lambda: "err" in results, timeout=5000)
    thread.join()
    assert "unknown external handoff action" in results["err"]
    from PySide6.QtWidgets import QApplication

    qtbot.waitUntil(lambda: QApplication.clipboard().text() == "copied", timeout=3000)


def test_main_thread_invoker_direct_and_queued(qtbot):
    invoker = MainThreadInvoker()
    assert invoker.call(lambda: 41 + 1) == 42
    import threading

    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "v", invoker.call(lambda: threading.current_thread() is threading.main_thread())
        )
    )
    t.start()
    qtbot.waitUntil(lambda: "v" in out, timeout=5000)
    assert out["v"] is True


@bash_only
def test_terminal_bridge_relays_output_and_input(qtbot, tmp_path):
    from harness.terminal.session import TerminalSession
    from harness.ui.viewers.terminal_view import TerminalBridge

    session = TerminalSession("t", "/bin/bash", str(tmp_path))
    session.start()
    try:
        assert session.wait_for_prompt(10)
        bridge = TerminalBridge(session)
        chunks = []
        bridge.output.connect(chunks.append)
        bridge.ready()  # replays scrollback (the prompt)
        assert chunks
        import base64

        bridge.input(base64.b64encode(b"echo bridged\n").decode())
        qtbot.waitUntil(
            lambda: b"bridged" in b"".join(base64.b64decode(c) for c in chunks), timeout=5000
        )
        bridge.resized(100, 40)
        assert session.cols == 100
        bridge.detach()
    finally:
        session.close()


@bash_only
def test_terminal_handoff_opens_view(qtbot, window, tmp_path):
    win, model, core = window
    model.script += [
        FakeModel.tool_call("terminal", command="", cwd=str(tmp_path), handoff=True),
        "Opened.",
    ]
    win.send_message("open the terminal for me")
    wait_turn(qtbot, win, timeout=20000)
    qtbot.waitUntil(lambda: "main" in win.terminal_panel.names(), timeout=10000)
    assert win.dock.active == "terminal" and win.side_panel.isVisibleTo(win)


@bash_only
def test_terminal_exit_marks_tab_and_restart_and_plus(qtbot, window, tmp_path):
    win, model, core = window
    win.show_view("terminal")
    qtbot.waitUntil(lambda: "main" in win.terminal_panel.alive_names(), timeout=10000)
    session = core.terminals.get("main")
    assert session.wait_for_prompt(10)
    session.write("exit\n")
    qtbot.waitUntil(lambda: win.terminal_panel._views["main"].exited, timeout=10000)
    assert win.terminal_panel.alive_names() == []
    assert "exited" in win.terminal_panel.tabs.tabText(0)
    assert win.terminal_panel._views["main"].exit_bar.isVisibleTo(win.terminal_panel)
    # Clicking the dock again gives a fresh shell under the same name.
    win.show_view("terminal")
    qtbot.waitUntil(lambda: "main" in win.terminal_panel.alive_names(), timeout=10000)
    assert core.terminals.get("main") is not session and core.terminals.get("main").alive
    assert win.terminal_panel.tabs.tabText(0) == "main"
    # The "+" button opens a second terminal in a new tab.
    win.terminal_panel.new_button.click()
    qtbot.waitUntil(lambda: "term-2" in win.terminal_panel.alive_names(), timeout=10000)
    assert win.terminal_panel.tabs.count() == 2 and win.terminal_panel.current_name() == "term-2"
    win.terminal_panel.tabs.tabCloseRequested.emit(1)
    qtbot.waitUntil(lambda: win.terminal_panel.tabs.count() == 1, timeout=5000)
    assert core.terminals.get("term-2") is None
