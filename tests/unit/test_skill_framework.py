from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from harness.config import Config
from harness.model.fake import FakeModel
from harness.model.summarizer import Summarizer
from harness.model.types import ToolCall
from harness.skills.base import (
    ApprovalDecision,
    ApprovalRequest,
    Handoff,
    Services,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
)
from harness.skills.registry import SkillContractError, SkillRegistry, tool_spec_for
from harness.skills.runner import ApprovalBroker, SkillRunner, auto_approve_broker
from harness.skills.schema import validate_args


class Echo(Skill):
    name = "echo"
    description = "Echo text back."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    handoff_description = "Nothing to hand off."

    def run(self, args, ctx):
        return SkillResult(content=args["text"], handoff=Handoff.done("Echoed"))


class Dangerous(Skill):
    name = "dangerous"
    description = "Needs approval."
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}
    needs_approval = True
    handoff_description = "Terminal."

    def approval_request(self, args, ctx):
        return ApprovalRequest(
            self.name,
            "Run command",
            args.get("command", ""),
            "command",
            editable_field="command",
            args=args,
        )

    def run(self, args, ctx):
        return SkillResult(content=f"ran {args['command']}")


class Slow(Skill):
    name = "slow"
    description = "Sleeps."
    parameters = {"type": "object", "properties": {}}
    handoff_description = "None."
    timeout_s = 0.2

    def run(self, args, ctx):
        time.sleep(2)
        return SkillResult(content="done")


class Crashy(Skill):
    name = "crashy"
    description = "Crashes."
    parameters = {"type": "object", "properties": {}}
    handoff_description = "None."

    def run(self, args, ctx):
        raise ZeroDivisionError("boom")


class Failing(Skill):
    name = "failing"
    description = "Fails politely."
    parameters = {"type": "object", "properties": {}}
    handoff_description = "None."

    def run(self, args, ctx):
        raise SkillError("file not found")


def registry_with(*skills):
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return reg


def ctx(tmp_path):
    return SkillContext(cwd=tmp_path, config=Config(), services=Services())


def test_contract_validation_reports_missing_pieces():
    class NoHandoff(Skill):
        name = "x"
        description = "d"
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):
            return SkillResult("")

    with pytest.raises(SkillContractError, match="handoff_description"):
        SkillRegistry().register(NoHandoff)

    class BadName(Echo):
        name = "Bad Name"

    with pytest.raises(SkillContractError, match="name"):
        SkillRegistry().register(BadName)

    class DeclaresHandoff(Echo):
        name = "dh"
        parameters = {"type": "object", "properties": {"handoff": {"type": "boolean"}}}

    with pytest.raises(SkillContractError, match="handoff"):
        SkillRegistry().register(DeclaresHandoff)

    with pytest.raises(SkillContractError, match="duplicate"):
        registry_with(Echo).register(Echo)


def test_tool_spec_adds_handoff_param_and_marks_approval():
    spec = tool_spec_for(Dangerous())
    assert "handoff" in spec.parameters["properties"]
    assert "asks the user to approve" in spec.description and "just call it" in spec.description
    assert "Handoff: Terminal." in spec.description


def test_discovery_from_directory(tmp_path):
    (tmp_path / "hello.py").write_text(
        "from harness.skills.base import Skill, SkillResult\n"
        "class Hello(Skill):\n"
        "    name = 'hello'\n    description = 'Say hi.'\n"
        "    parameters = {'type': 'object', 'properties': {}}\n"
        "    handoff_description = 'None.'\n"
        "    def run(self, args, ctx):\n        return SkillResult('hi')\n"
    )
    (tmp_path / "broken.py").write_text("import nonexistent_module_xyz\n")
    (tmp_path / "_private.py").write_text("raise RuntimeError('should not import')\n")
    reg = SkillRegistry()
    reg.load_directory(tmp_path)
    assert reg.names() == ["hello"]
    assert len(reg.problems) == 1 and "broken.py" in reg.problems[0].source


def test_builtin_skills_all_load():
    reg = SkillRegistry()
    reg.load_builtin()
    assert reg.problems == [], reg.problems
    names = set(reg.names())
    expected = {
        "terminal",
        "web_fetch",
        "pdf_read",
        "read_file",
        "list_directory",
        "find_files",
        "grep_files",
        "file_info",
        "git_inspect",
        "view_image",
        "write_file",
        "web_search",
        "open_with_default_app",
        "copy_to_clipboard",
        "system_info",
        "process_list",
        "background_command",
        "task_list",
        "notify",
        "get_url_raw",
    }
    assert expected <= names, expected - names
    for spec in reg.tool_specs():
        assert spec.parameters["type"] == "object"


def test_runner_happy_path_and_handoff_flag(tmp_path):
    runner = SkillRunner(registry_with(Echo), auto_approve_broker())
    out = runner.execute(ToolCall("c1", "echo", {"text": "hi", "handoff": True}), ctx(tmp_path))
    assert out.result.ok and out.result.content == "hi"
    assert out.handoff_now is True
    assert out.result.handoff.kind == "done"


def test_runner_reports_unknown_invalid_and_parse_errors(tmp_path):
    runner = SkillRunner(registry_with(Echo), auto_approve_broker())
    c = ctx(tmp_path)
    assert "unknown skill" in runner.execute(ToolCall("c", "nope", {}), c).result.content
    assert (
        "missing required argument 'text'"
        in runner.execute(ToolCall("c", "echo", {}), c).result.content
    )
    assert (
        "should be string" in runner.execute(ToolCall("c", "echo", {"text": 5}), c).result.content
    )
    out = runner.execute(
        ToolCall("c", "echo", raw_arguments="{bad", parse_error="arguments are not valid JSON"), c
    )
    assert not out.result.ok and "not valid JSON" in out.result.content


def test_runner_timeout_crash_and_skill_error(tmp_path):
    runner = SkillRunner(registry_with(Slow, Crashy, Failing), auto_approve_broker())
    c = ctx(tmp_path)
    out = runner.execute(ToolCall("c", "slow", {}), c)
    assert not out.result.ok and "timed out after 0.2s" in out.result.content
    out = runner.execute(ToolCall("c", "crashy", {}), ctx(tmp_path))
    assert (
        not out.result.ok
        and "ZeroDivisionError" in out.result.content
        and "Traceback" in out.result.content
    )
    out = runner.execute(ToolCall("c", "failing", {}), ctx(tmp_path))
    assert out.result.content == "Error: file not found"


def test_approval_flow_with_summary_reject_and_edit(tmp_path):
    seen = []

    def handler(pending):
        seen.append(pending.request)
        # The request is shown before the summary is known; the summary arrives asynchronously.
        assert pending.summary_pending or pending.request.summary is not None
        pending.event.wait(0.01)
        for _ in range(100):
            if not pending.summary_pending:
                break
            time.sleep(0.01)
        if pending.request.detail == "rm -rf /":
            broker.resolve(pending.id, ApprovalDecision.reject("too dangerous"))
        else:
            broker.resolve(pending.id, ApprovalDecision.approve({"command": "ls -la"}))

    broker = ApprovalBroker(handler)
    summarizer = Summarizer(FakeModel(script=["Deletes everything.", "Lists files."]))
    runner = SkillRunner(registry_with(Dangerous), broker, summarizer)
    out = runner.execute(ToolCall("c", "dangerous", {"command": "rm -rf /"}), ctx(tmp_path))
    assert not out.result.ok
    assert "rejected" in out.result.content and "too dangerous" in out.result.content
    assert seen[0].summary == "Deletes everything."
    out = runner.execute(ToolCall("c", "dangerous", {"command": "ls"}), ctx(tmp_path))
    assert out.result.content == "ran ls -la"
    assert out.args["command"] == "ls -la"


def test_approval_offline_summarizer_still_works(tmp_path):
    broker = auto_approve_broker()
    seen = []
    inner = broker.handler
    broker.handler = lambda p: (seen.append(p.request), inner(p))
    runner = SkillRunner(
        registry_with(Dangerous), broker, Summarizer(FakeModel(offline_reason="down"))
    )
    out = runner.execute(ToolCall("c", "dangerous", {"command": "ls"}), ctx(tmp_path))
    assert out.result.ok
    assert seen[0].summary.startswith("Summarizer model offline")


def test_no_handler_rejects_and_cancel_unblocks(tmp_path):
    runner = SkillRunner(registry_with(Dangerous), ApprovalBroker())
    out = runner.execute(ToolCall("c", "dangerous", {"command": "ls"}), ctx(tmp_path))
    assert "rejected" in out.result.content
    broker = ApprovalBroker(lambda p: None)  # UI never answers
    c = ctx(tmp_path)
    threading.Timer(0.2, c.cancel.cancel).start()
    out = SkillRunner(registry_with(Dangerous), broker).execute(
        ToolCall("c", "dangerous", {"command": "ls"}), c
    )
    assert "cancelled" in out.result.content


def test_result_truncation_keeps_full_text(tmp_path):
    runner = SkillRunner(registry_with(Echo), auto_approve_broker(), max_result_chars=10)
    out = runner.execute(ToolCall("c", "echo", {"text": "x" * 50}), ctx(tmp_path))
    assert out.result.content.startswith("x" * 10) and "truncated" in out.result.content
    assert out.result.data["full_content"] == "x" * 50


def test_validate_args_details():
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "p": {"type": ["string", "null"]},
        },
        "required": ["n"],
    }
    assert validate_args(schema, {"n": 1, "mode": "a", "p": None}) == []
    assert validate_args(schema, {"n": True}) == ["argument 'n' should be an integer, got boolean"]
    assert "must be one of" in validate_args(schema, {"n": 1, "mode": "z"})[0]
    assert validate_args(schema, "nope") == ["arguments must be a JSON object"]


def test_context_resolve_and_need(tmp_path):
    c = ctx(tmp_path)
    assert c.resolve("a/b") == (tmp_path / "a" / "b").resolve()
    with pytest.raises(SkillError):
        c.need("fetcher")
    assert isinstance(Path(c.resolve("~")), Path)
