from __future__ import annotations

import base64
import json

import httpx
import pytest

from harness.config import Config, StackComponent, StackConfig
from harness.model.fake import FakeModel
from harness.model.stack import ModelStack, StackError, plan
from harness.model.types import Message, ToolCall
from harness.skills.base import RecordingUi, Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import SkillRunner, auto_approve_broker

ROUTER = "http://router:18080"
GEN = "http://gen:18083"


def components(**overrides) -> dict[str, StackComponent]:
    comps = {
        "main": StackComponent(kind="router", name="big", url=ROUTER, gib=70, priority=100),
        "summarizer": StackComponent(
            kind="router", name="small", url=ROUTER + "/v1", gib=35, priority=50
        ),
        "image": StackComponent(kind="service", name="image", url=GEN, gib=36),
        "mesh": StackComponent(kind="service", name="mesh", url=GEN, gib=9),
    }
    for key, changes in overrides.items():
        comps[key] = comps[key].model_copy(update=changes)
    return comps


# -- planning ------------------------------------------------------------------------


def test_plan_unloads_lowest_priority_only_as_far_as_needed():
    comps = components()
    steps = plan(["image"], {"main", "summarizer"}, comps, 114)
    assert steps.unload == ["summarizer"] and steps.load == ["image"]
    steps = plan(["mesh"], {"main", "summarizer"}, comps, 114)
    assert steps.empty is False and steps.unload == [] and steps.load == ["mesh"]  # 114 fits
    assert plan(["main"], {"main", "summarizer"}, comps, 114).empty


def test_plan_swaps_the_whole_stack_when_the_job_is_big():
    comps = components(image={"gib": 90})
    steps = plan(["image"], {"main", "summarizer"}, comps, 114)
    assert steps.unload == ["summarizer", "main"] and steps.load == ["image"]


def test_plan_exclusive_component_runs_alone_and_evicts_generation_first():
    comps = components(image={"exclusive": True})
    assert plan(["image"], {"main", "summarizer", "mesh"}, comps, 114).unload == [
        "mesh",
        "summarizer",
        "main",
    ]


def test_plan_rejects_what_can_never_fit_and_unknown_names():
    with pytest.raises(StackError, match="budget"):
        plan(["main", "summarizer", "image"], set(), components(), 114)
    with pytest.raises(StackError, match="unknown"):
        plan(["video"], set(), components(), 114)


def test_url_normalised_without_v1():
    assert components()["summarizer"].url == ROUTER


# -- the stack against fake servers ----------------------------------------------------


class FakeMachine:
    """A router (instant loads reported as 'loading' once) and a generation service."""

    def __init__(self, router_loaded=("big", "small"), service_loaded=()):
        self.router = {name: "loaded" for name in router_loaded}
        self.service = set(service_loaded)
        self.log: list[str] = []
        self.jobs: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path, host = request.url.path, request.url.host
        if host == "router":
            if path == "/models":
                data = [{"id": n, "status": {"value": v}} for n, v in self.router.items()]
                for name, value in self.router.items():  # a load finishes after one poll
                    if value == "loading":
                        self.router[name] = "loaded"
                return httpx.Response(200, json={"data": data})
            if path == "/models/unload":
                self.log.append(f"unload {body['model']}")
                self.router.pop(body["model"], None)
                return httpx.Response(200, json={"success": True})
            if path == "/models/load":
                self.log.append(f"load {body['model']}")
                self.router[body["model"]] = "loading"
                return httpx.Response(200, json={"success": True})
        if host == "gen":
            if path == "/health":
                comps = {n: {"loaded": n in self.service} for n in ("image", "mesh")}
                return httpx.Response(200, json={"components": comps})
            if path == "/load":
                self.log.append(f"load {body['component']}")
                self.service.add(body["component"])
                return httpx.Response(200, json={"loaded": body["component"]})
            if path == "/unload":
                self.log.append(f"unload {body['component']}")
                self.service.discard(body["component"])
                return httpx.Response(200, json={"unloaded": [body["component"]]})
        return httpx.Response(404, text=f"no route {host}{path}")


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.setattr("harness.model.stack.POLL_S", 0.0)
    return FakeMachine()


def make_stack(machine, **overrides) -> ModelStack:
    config = StackConfig(budget_gib=114, components=components(**overrides))
    return ModelStack(config, transport=httpx.MockTransport(machine.handler))


def test_acquire_then_restore_hands_the_machine_back(machine):
    stack = make_stack(machine)
    assert stack.resident() == {"main", "summarizer"} and not stack.dirty
    said = []
    stack.acquire(["image"], progress=said.append)
    assert machine.log == ["unload small", "load image"]
    assert stack.resident() == {"main", "image"} and stack.dirty
    assert any("small" in s for s in said) and any("image" in s for s in said)
    stack.acquire(["mesh"])  # image + mesh + main = 115 > 114: the image goes, not main
    assert machine.log[2:] == ["unload image", "load mesh"]
    stack.restore()  # 70 + 35 + 9 fits the budget exactly: the mesh model may stay
    assert machine.log[4:] == ["load small"]
    assert stack.resident() == {"main", "summarizer", "mesh"} and not stack.dirty
    stack.restore()  # nothing to do
    assert len(machine.log) == 5


def test_restore_keeps_a_skill_component_that_still_fits(machine):
    machine.router.pop("small")  # the summarizer was not loaded to begin with
    stack = make_stack(machine)
    stack.acquire(["mesh"])
    assert machine.log == ["load mesh"]
    stack.restore()  # nothing was evicted: mesh stays for the next job, the service reaps it
    assert machine.log == ["load mesh"] and not stack.dirty


def test_whole_stack_swap_restores_main_first(machine):
    stack = make_stack(machine, image={"gib": 100})
    stack.acquire(["image"])
    assert machine.log == ["unload small", "unload big", "load image"]
    stack.restore()
    assert machine.log[3:] == ["unload image", "load big", "load small"]


def test_restore_never_unloads_what_was_there_before(machine):
    # Already over a (too small) budget before the skill: restoring must not "fix" that by
    # unloading the main model, which is what an unpinned plan does.
    stack = make_stack(machine)
    stack.config.budget_gib = 100
    stack.acquire(["mesh"])
    assert machine.log == ["unload small", "load mesh"]
    stack.restore()
    assert machine.log[2:] == ["unload mesh", "load small"]
    assert stack.resident() == {"main", "summarizer"}


def test_failed_restore_is_retried(machine):
    stack = make_stack(machine)
    stack.acquire(["image"])
    real = machine.handler

    def broken(request):
        if request.url.path == "/models/load":
            return httpx.Response(503, text="busy")
        return real(request)

    stack._clients.clear()
    stack._transport = httpx.MockTransport(broken)
    with pytest.raises(StackError):
        stack.restore()
    assert stack.dirty
    stack._clients.clear()
    stack._transport = httpx.MockTransport(real)
    stack.restore()
    assert not stack.dirty and "summarizer" in stack.resident()


def test_server_errors_become_stack_errors(machine):
    stack = make_stack(machine, image={"url": "http://nowhere:1"})
    with pytest.raises(StackError):
        stack.acquire(["image"])


# -- the agent hands back to the main model ------------------------------------------


class FakeStack:
    def __init__(self):
        self.dirty = False
        self.restores = 0

    def acquire(self, need, progress=None, cancel=None):
        self.dirty = True

    def restore(self, progress=None, cancel=None):
        self.restores += 1
        self.dirty = False


def test_agent_restores_before_the_next_model_call(tmp_path, monkeypatch):
    from harness.agent.loop import Agent
    from harness.agent.store import SessionStore

    cfg = Config()
    cfg.sessions.db_path = str(tmp_path / "s.sqlite3")
    stack = FakeStack()
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, stack.dirty, stack.restores))
        png = base64.b64encode(b"\x89PNG fake").decode()
        return httpx.Response(200, json={"png_base64": png, "seed": 7, "seconds": 1.0})

    monkeypatch.setattr("harness.skills.builtin._generation.httpx.post", fake_post)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    registry = SkillRegistry()
    registry.load_builtin()
    services = Services(ui=RecordingUi(), stack=stack)
    call = ToolCall("c1", "generate_image", {"prompt": "a lizard on a rock"})
    script = [Message("assistant", "", tool_calls=[call]), "Here it is."]
    seen_dirty = []
    model = FakeModel(script=script)
    original = model.stream_chat

    def watching(*args, **kwargs):
        seen_dirty.append(stack.dirty)
        return original(*args, **kwargs)

    model.stream_chat = watching
    agent = Agent(
        cfg, model, registry, SkillRunner(registry, auto_approve_broker()), services,
        SessionStore(cfg.sessions.db_path),
    )  # fmt: skip
    agent.new_session(tmp_path)
    agent.send("draw me a lizard")
    assert calls and calls[0][1] is True  # the job ran with the image model in
    assert seen_dirty == [False, False]  # the main model was never called while swapped out
    assert stack.restores == 1
    tool = [m for m in agent.session.messages if m.role == "tool"][0]
    assert "seed 7" in tool.content and (tmp_path / "home" / "generated").is_dir()


# -- the skills -----------------------------------------------------------------------


def run_skill(tmp_path, name, args, stack=None):
    registry = SkillRegistry()
    registry.load_builtin()
    runner = SkillRunner(registry, auto_approve_broker())
    ui = RecordingUi()
    ctx = SkillContext(cwd=tmp_path, config=Config(), services=Services(ui=ui, stack=stack))
    return runner.execute(ToolCall("c1", name, args), ctx), ui


def test_generate_3d_model_from_prompt_chains_image_then_mesh(tmp_path, monkeypatch):
    stack = FakeStack()
    needs = []
    stack.acquire = lambda need, progress=None, cancel=None: needs.append(list(need))
    posted = []

    def fake_post(url, json, timeout):
        posted.append(url)
        blob = base64.b64encode(b"data").decode()
        if url.endswith("/image"):
            return httpx.Response(200, json={"png_base64": blob, "seed": 3, "seconds": 2})
        return httpx.Response(
            200,
            json={
                "glb_base64": blob,
                "preview_png_base64": blob,
                "seed": 3,
                "vertices": 10,
                "faces": 20,
                "seconds": 5,
            },
        )

    monkeypatch.setattr("harness.skills.builtin._generation.httpx.post", fake_post)
    outcome, ui = run_skill(
        tmp_path, "generate_3d_model", {"prompt": "a teapot", "path": "pot.glb"}, stack
    )
    assert outcome.result.ok, outcome.result.content
    assert needs == [["image"], ["mesh"]]
    assert [u.rsplit("/", 1)[1] for u in posted] == ["image", "mesh"]
    assert (tmp_path / "pot.glb").exists() and (tmp_path / "pot-reference.png").exists()
    assert (tmp_path / "pot-preview.png").exists()
    assert outcome.result.handoff.target == str(tmp_path / "pot.glb")
    outcome, _ = run_skill(tmp_path, "generate_3d_model", {"prompt": "x", "path": "pot.glb"})
    assert not outcome.result.ok and "already exists" in outcome.result.content


def test_generate_3d_model_needs_exactly_one_source(tmp_path):
    outcome, _ = run_skill(tmp_path, "generate_3d_model", {})
    assert not outcome.result.ok and "exactly one" in outcome.result.content


def test_generation_service_error_is_reported(tmp_path, monkeypatch):
    def fake_post(url, json, timeout):
        return httpx.Response(500, json={"detail": "OutOfMemoryError: HIP out of memory"})

    monkeypatch.setattr("harness.skills.builtin._generation.httpx.post", fake_post)
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "home"))
    outcome, _ = run_skill(tmp_path, "generate_image", {"prompt": "a cat"})
    assert not outcome.result.ok and "HIP out of memory" in outcome.result.content
