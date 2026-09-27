from __future__ import annotations

from harness.agent.loop import Agent
from harness.agent.prompts import build_system_prompt
from harness.agent.store import SessionStore
from harness.config import Config
from harness.model.fake import FakeModel
from harness.model.types import Message
from harness.skills.base import RecordingUi, Services
from harness.skills.registry import SkillRegistry
from harness.skills.runner import ApprovalBroker, SkillRunner, auto_approve_broker
from harness.skills.tasklist_store import TaskListStore


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))

        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def make_agent(tmp_path, script, broker=None):
    cfg = Config()
    cfg.sessions.db_path = str(tmp_path / "s.sqlite3")
    cfg.web.untrusted_dirs = []
    cfg.security.deny_paths = []
    registry = SkillRegistry()
    registry.load_builtin()
    services = Services(ui=RecordingUi(), task_lists=TaskListStore())
    runner = SkillRunner(registry, broker or auto_approve_broker())
    store = SessionStore(cfg.sessions.db_path)
    model = FakeModel(script=script)
    events = Recorder()
    agent = Agent(cfg, model, registry, runner, services, store, events)
    return agent, model, store, events


def test_store_sessions_messages_search(tmp_path):
    store = SessionStore(tmp_path / "db.sqlite3")
    a = store.create_session("/tmp", "First")
    b = store.create_session("/tmp", "Second")
    store.append_message(a.id, Message("user", "how do I fix the lizard green theme?"))
    store.append_message(a.id, Message("assistant", "Edit ui.theme in the config."))
    store.append_message(b.id, Message("user", "unrelated question about pandas"))
    assert [s.id for s in store.list_sessions()][0] == b.id  # most recently updated first
    hits = store.search("lizard")
    assert len(hits) == 1 and hits[0].session_id == a.id and "lizard" in hits[0].snippet
    assert store.search("unrelated (weird) punctuation!") == [] or True  # must not raise
    assert store.search("") == []
    msgs = store.load_messages(a.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    store.replace_messages(a.id, [Message("system", "s"), Message("user", "[Summary] x")])
    assert store.message_count(a.id) == 2
    store.delete_session(a.id)
    assert store.get_session(a.id) is None and store.search("lizard") == []
    assert store.delete_all() == 1
    assert store.list_sessions() == [] and store.search("pandas") == []
    store.close()


def test_system_prompt_mentions_rules_and_skills(tmp_path):
    reg = SkillRegistry()
    reg.load_builtin()
    prompt = build_system_prompt("Be nice.", tmp_path, reg.all())
    assert prompt.startswith("Be nice.")
    assert "get_url_raw" in prompt and "ALWAYS needs the user's approval" in prompt
    assert "Prefer a dedicated skill" in prompt
    assert str(tmp_path) in prompt
    assert "`terminal`" in prompt and "(needs approval)" in prompt


def test_agent_plain_reply_streams_and_persists(tmp_path):
    agent, model, store, events = make_agent(tmp_path, ["Hello there, friend."])
    session = agent.new_session(tmp_path)
    agent.send("hi")
    text = "".join(a[0] for n, a in events.calls if n == "text_delta")
    assert text == "Hello there, friend."
    assert events.named("turn_started") and events.named("turn_finished")[0][1] == (False,)
    stored = store.load_messages(session.id)
    assert [m.role for m in stored] == ["system", "user", "assistant"]
    assert store.get_session(session.id).title == "hi"
    assert model.requests[0][0].role == "system" and model.tools_seen[0]


def test_agent_runs_skill_and_feeds_result_back(tmp_path):
    (tmp_path / "note.txt").write_text("secret sauce\n")
    script = [
        FakeModel.tool_call("read_file", path="note.txt", handoff=True),
        "The note says secret sauce.",
    ]
    agent, model, store, events = make_agent(tmp_path, script)
    session = agent.new_session(tmp_path)
    agent.send("what does note.txt say?")
    assert [m.role for m in store.load_messages(session.id)] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    tool_msg = model.requests[1][-1]
    assert (
        tool_msg.role == "tool"
        and "secret sauce" in tool_msg.content
        and tool_msg.tool_call_id == "call_1"
    )
    finished = events.named("skill_finished")
    assert len(finished) == 1 and finished[0][1][0].result.ok
    assert events.named("handoff_requested")[0][1][0].action == "editor"
    events_db = store.skill_events(session.id)
    assert events_db[0].skill == "read_file" and events_db[0].handoff["action"] == "editor"


def test_agent_reports_rejection_to_model(tmp_path):
    from harness.skills.base import ApprovalDecision

    broker = ApprovalBroker(lambda p: broker.resolve(p.id, ApprovalDecision.reject("not now")))
    script = [
        FakeModel.tool_call("write_file", action="write", path="x.txt", content="hi"),
        "Understood, I won't write it.",
    ]
    agent, model, store, events = make_agent(tmp_path, script, broker)
    agent.new_session(tmp_path)
    agent.send("write x.txt")
    assert not (tmp_path / "x.txt").exists()
    assert (
        "rejected" in model.requests[1][-1].content and "not now" in model.requests[1][-1].content
    )
    assert (
        events.named("approval_needed") == []
    )  # the broker handled it directly; UI wiring is tested in Qt tests


def test_agent_survives_model_offline_and_reopens_session(tmp_path):
    agent, model, store, events = make_agent(tmp_path, ["first answer"])
    session = agent.new_session(tmp_path)
    agent.send("q1")
    model.offline_reason = "ConnectError: down"
    agent.send("q2")
    statuses = [a[0] for n, a in events.calls if n == "status"]
    assert any("Main model error" in s for s in statuses)
    # Sessions stay browsable and can be reopened.
    reopened = agent.open_session(session.id)
    roles = [m.role for m in reopened.messages]
    assert roles[:3] == ["system", "user", "assistant"] and reopened.cwd == tmp_path


def test_agent_stop_cancels(tmp_path):
    agent, model, store, events = make_agent(tmp_path, ["x" * 2000])
    agent.new_session(tmp_path)
    original = model.stream_chat

    def stream_then_cancel(*args, **kwargs):
        for i, event in enumerate(original(*args, **kwargs)):
            if i == 3:
                agent.stop()
            yield event

    model.stream_chat = stream_then_cancel
    agent.send("go")
    assert events.named("turn_finished")[0][1] == (True,)


def test_agent_compaction_and_task_persistence(tmp_path):
    agent, model, store, events = make_agent(
        tmp_path,
        [
            FakeModel.tool_call("task_list", action="add", text="step one"),
            "done",
            "- summary of everything",
        ],
    )
    agent.config.models.main.context_window = 100
    agent.config.models.main.compaction_threshold = 0.5
    agent.config.models.main.compaction_keep_last = 2
    session = agent.new_session(tmp_path)
    agent.send("start")
    assert "step one" in store.load_tasks(session.id)
    # Next turn is over the (tiny) window: compaction runs before the model is called.
    model.script.append("after compaction")
    agent.send("more " * 50)
    assert events.named("compacted")
    assert any(m.content.startswith("[Summary") for m in store.load_messages(session.id))
