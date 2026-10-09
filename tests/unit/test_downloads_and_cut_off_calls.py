"""The STEP-file bug: a tool call cut off at max_tokens poisoned the session, and saving a
file from the web had no path except through the model's own output."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from harness.config import Config, Endpoint
from harness.model.client import OpenAICompatClient
from harness.model.types import Message, StreamDone, StreamError, ToolCall, ToolSpec
from harness.security.net import NetPolicy
from harness.security.paths import PathPolicy
from harness.skills.base import RecordingUi, Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker
from harness.web.fetch import FetchError, SafeFetcher

# -- a broken call never goes back to the server ------------------------------------------


def test_broken_arguments_are_replayed_as_valid_json():
    cut = ToolCall("c1", "write_file", raw_arguments='{"path": "a.step", "content": "ISO-1030')
    cut.parse_error = "arguments are not valid JSON"
    assert json.loads(cut.api_arguments()) == {}
    good = ToolCall("c2", "read_file", {"path": "x"}, raw_arguments='{"path": "x"}')
    assert good.api_arguments() == '{"path": "x"}'
    # A session saved before this fix has the raw text but no parse_error: still repaired.
    old = Message("assistant", "", tool_calls=[ToolCall("c3", "w", raw_arguments='{"a": "')])
    assert old.to_api()["tool_calls"][0]["function"]["arguments"] == "{}"
    assert json.loads(ToolCall("c4", "w", raw_arguments="[1, 2]").api_arguments()) == {}


def sse(chunks):
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def client(handler):
    return OpenAICompatClient(
        Endpoint(base_url="http://model.test/v1"), transport=httpx.MockTransport(handler)
    )


TOOLS = [ToolSpec("write_file", "Write", {"type": "object", "properties": {}})]


def test_a_call_cut_off_at_the_output_limit_says_so():
    def handler(request):
        start = {"index": 0, "id": "c1", "function": {"name": "write_file", "arguments": ""}}
        part = {"index": 0, "function": {"arguments": '{"path": "a.step", "content": "ISO-1'}}
        chunks = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [start]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [part]}, "finish_reason": "length"}]},
        ]
        return httpx.Response(200, content=sse(chunks))

    events = list(client(handler).stream_chat([Message("user", "save it")], TOOLS, max_tokens=4096))
    done = next(e for e in events if isinstance(e, StreamDone))
    error = done.message.tool_calls[0].parse_error
    assert "output limit (4096 tokens)" in error and "download_url" in error


def test_a_server_error_reports_its_message_instead_of_crashing():
    """It used to raise httpx.ResponseNotRead and the turn crashed with "Internal error"."""

    def handler(request):
        return httpx.Response(
            500, json={"error": {"message": "Failed to parse tool call arguments"}}
        )

    events = list(client(handler).stream_chat([Message("user", "hi")], TOOLS))
    assert isinstance(events[-1], StreamError)
    assert (
        "HTTP 500" in events[-1].error and "Failed to parse tool call arguments" in events[-1].error
    )


# -- download_url --------------------------------------------------------------------------

STEP = b"ISO-10303-21;\nHEADER;\n" + b"#1=CARTESIAN_POINT('',(0.,0.,0.));\n" * 20000  # ~700 KB


@pytest.fixture
def web(tmp_path):
    def handler(request):
        if request.url.path == "/old.step":
            return httpx.Response(302, headers={"location": "/parts/BH-18650-PC.STEP"})
        if request.url.path == "/parts/BH-18650-PC.STEP":
            return httpx.Response(200, headers={"content-type": "model/step"}, content=STEP)
        if request.url.path == "/to-private":
            return httpx.Response(302, headers={"location": "http://router.local/admin"})
        return httpx.Response(404)

    table = {"parts.example": ["93.184.216.34"], "router.local": ["192.168.1.1"]}
    policy = NetPolicy.from_config([], "http://10.0.0.228:18082", lambda h: table[h])
    cfg = Config()
    cfg.web.untrusted_dirs = [str(tmp_path / "downloads")]
    fetcher = SafeFetcher(policy, cfg.web, transport=httpx.MockTransport(handler))
    return fetcher, cfg


def test_fetcher_download_streams_to_disk_and_follows_checked_redirects(web, tmp_path):
    fetcher, _ = web
    target = tmp_path / "out" / "battery.step"
    final, size, ctype = fetcher.download("https://parts.example/old.step", target, 10 * 2**20)
    assert final.endswith("/parts/BH-18650-PC.STEP") and size == len(STEP) and ctype == "model/step"
    assert target.read_bytes() == STEP and not target.with_name("battery.step.part").exists()
    with pytest.raises(FetchError, match="larger than the download limit"):
        fetcher.download("https://parts.example/old.step", tmp_path / "big.step", 1000)
    assert not (tmp_path / "big.step").exists() and not (tmp_path / "big.step.part").exists()
    from harness.security.net import BlockedAddress

    with pytest.raises(BlockedAddress):  # a redirect into the private network is refused
        fetcher.download("https://parts.example/to-private", tmp_path / "x", 10 * 2**20)


def test_download_url_skill(web, tmp_path, monkeypatch):
    fetcher, cfg = web
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    registry = SkillRegistry()
    registry.load_builtin()
    approvals = []
    broker = auto_approve_broker()
    original = broker.request
    broker.request = lambda request, cancel=None: (
        approvals.append(request) or original(request, cancel)
    )
    services = Services(
        fetcher=fetcher, path_policy=PathPolicy.from_config(cfg.security, cfg.web), ui=RecordingUi()
    )
    ctx = SkillContext(cwd=tmp_path, config=cfg, services=services)
    runner = SkillRunner(registry, broker)

    def download(**args):
        return runner.execute(ToolCall("c", "download_url", args), ctx).result

    out = download(url="https://parts.example/parts/BH-18650-PC.STEP")
    saved = tmp_path / "home" / "downloads" / "BH-18650-PC.STEP"
    assert out.ok and saved.read_bytes() == STEP
    assert hashlib.sha256(STEP).hexdigest() in out.content
    assert "ISO-10303" not in out.content  # the model never sees the file itself
    assert approvals and "-> " + str(saved) in approvals[0].detail  # you see where it goes
    again = download(url="https://parts.example/parts/BH-18650-PC.STEP")
    assert not again.ok and "already exists" in again.content
    (tmp_path / "cad").mkdir()
    into_folder = download(url="https://parts.example/old.step", path="cad")
    assert into_folder.ok and (tmp_path / "cad" / "old.step").exists()
