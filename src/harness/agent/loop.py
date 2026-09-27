"""The agent loop (P1-P5, P8): model turn -> skill calls -> approvals -> results -> model turn...

Runs on a worker thread and reports through :class:`AgentEvents`, which the Qt
layer implements with signals. Nothing in here imports Qt.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness.agent.prompts import build_system_prompt
from harness.agent.store import SessionStore
from harness.config import Config
from harness.model import text_tools
from harness.model.client import ChatModel
from harness.model.compaction import compact, needs_compaction
from harness.model.types import (
    CancelToken,
    Message,
    ModelCancelled,
    ModelError,
    StreamDone,
    StreamError,
    TextDelta,
)
from harness.paths import HarnessPaths
from harness.skills.base import Handoff, Services, SkillContext
from harness.skills.registry import SkillRegistry
from harness.skills.runner import PendingApproval, SkillOutcome, SkillRunner

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 40


class AgentEvents(Protocol):
    """Callbacks from the agent thread. Implementations must be thread-safe (Qt signals are)."""

    def turn_started(self) -> None: ...
    def text_delta(self, text: str) -> None: ...
    def assistant_message(self, message: Message) -> None: ...
    def skill_started(self, call_id: str, skill: str, args: dict) -> None: ...
    def approval_needed(self, pending: PendingApproval) -> None: ...
    def skill_finished(self, outcome: SkillOutcome) -> None: ...
    def handoff_requested(self, handoff: Handoff) -> None: ...
    def status(self, text: str, error: bool = False) -> None: ...
    def compacted(self, summary: str) -> None: ...
    def turn_finished(self, cancelled: bool) -> None: ...


class NullEvents:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@dataclass
class Session:
    id: str
    cwd: Path
    messages: list[Message]
    project_id: str | None = None


class Agent:
    """One agent per open session. ``send`` blocks until the turn is complete; the UI calls it on a thread."""

    def __init__(
        self,
        config: Config,
        model: ChatModel,
        registry: SkillRegistry,
        runner: SkillRunner,
        services: Services,
        store: SessionStore,
        events: AgentEvents | None = None,
        paths: HarnessPaths | None = None,
    ) -> None:
        self.config = config
        self.paths = paths or HarnessPaths()
        self.model = model
        self.registry = registry
        self.runner = runner
        self.services = services
        self.store = store
        self.events: AgentEvents = events or NullEvents()
        self.session: Session | None = None
        self.cancel = CancelToken()
        self._busy = threading.Lock()

    # -- sessions ------------------------------------------------------------

    def new_session(self, cwd: Path | None = None, project_id: str | None = None) -> Session:
        project = self.store.get_project(project_id)
        if cwd is None and project is not None and project.root_dir:
            cwd = Path(project.root_dir)
        cwd = cwd or Path(self.config.sessions.default_cwd or Path.home())
        record = self.store.create_session(str(cwd), project_id=project.id if project else None)
        self.session = Session(record.id, Path(record.cwd), [], record.project_id)
        self._ensure_system_prompt()
        return self.session

    def open_session(self, session_id: str) -> Session:
        record = self.store.get_session(session_id)
        if record is None:
            raise KeyError(session_id)
        self.session = Session(
            record.id, Path(record.cwd), self.store.load_messages(session_id), record.project_id
        )
        if self.services.task_lists is not None:
            self.services.task_lists.load(session_id, self.store.load_tasks(session_id))
        self._ensure_system_prompt()
        return self.session

    def set_cwd(self, cwd: Path) -> None:
        assert self.session is not None
        self.session.cwd = cwd
        self.store.set_cwd(self.session.id, str(cwd))
        self._refresh_system_prompt()

    def set_project(self, project_id: str | None) -> None:
        assert self.session is not None
        self.session.project_id = project_id
        self.store.set_session_project(self.session.id, project_id)
        self._refresh_system_prompt()

    def global_context(self) -> str:
        path = self.paths.global_context_file
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError as exc:
            log.warning("cannot read %s: %s", path, exc)
            return ""

    def _ensure_system_prompt(self) -> None:
        assert self.session is not None
        if not self.session.messages or self.session.messages[0].role != "system":
            self.session.messages.insert(0, Message("system", self._system_prompt()))
            if self.store.message_count(self.session.id) == 0:
                self.store.append_message(self.session.id, self.session.messages[0])
        else:
            self._refresh_system_prompt()

    def _refresh_system_prompt(self) -> None:
        assert self.session is not None
        self.session.messages[0] = Message("system", self._system_prompt())

    def _system_prompt(self) -> str:
        assert self.session is not None
        project = self.store.get_project(self.session.project_id)
        return build_system_prompt(
            self.config.prompt.startup,
            self.session.cwd,
            self.registry.all(),
            global_context=self.global_context(),
            project_name=project.name if project else None,
            project_root=project.root_dir if project else None,
            project_instructions=project.instructions if project else None,
        )

    # -- turns -----------------------------------------------------------------

    def stop(self) -> None:
        self.cancel.cancel()

    def send(self, text: str) -> None:
        """Run one user turn to completion (including any number of skill rounds)."""
        assert self.session is not None, "no session open"
        if not self._busy.acquire(blocking=False):
            self.events.status("The agent is still working on the previous message.", error=True)
            return
        self.cancel = CancelToken()
        cancelled = False
        try:
            self.events.turn_started()
            self._append(Message("user", text))
            if (
                self.store.get_session(self.session.id)
                and self.store.get_session(self.session.id).title == "New session"
            ):
                self.store.rename_session(
                    self.session.id, text.strip().splitlines()[0][:60] or "New session"
                )
            self._maybe_compact()
            for _round in range(MAX_TOOL_ROUNDS):
                reply = self._model_turn()
                if reply is None:
                    cancelled = self.cancel.cancelled
                    break
                if not reply.tool_calls:
                    break
                self._run_skills(reply)
                if self.cancel.cancelled:
                    cancelled = True
                    break
            else:
                self.events.status(
                    f"Stopped after {MAX_TOOL_ROUNDS} skill rounds in one turn.", error=True
                )
        except Exception as exc:  # noqa: BLE001 - the UI must survive anything (P13)
            log.exception("agent turn crashed")
            self.events.status(f"Internal error: {exc.__class__.__name__}: {exc}", error=True)
        finally:
            self._busy.release()
            self.events.turn_finished(cancelled)

    def compact_now(self) -> bool:
        assert self.session is not None
        return self._compact(force=True)

    # -- internals ---------------------------------------------------------------

    def _append(self, message: Message) -> int:
        assert self.session is not None
        self.session.messages.append(message)
        return self.store.append_message(self.session.id, message)

    def _model_turn(self) -> Message | None:
        assert self.session is not None
        tools = self.registry.tool_specs()
        final: Message | None = None
        try:
            for event in self.model.stream_chat(self.session.messages, tools, cancel=self.cancel):
                if isinstance(event, TextDelta):
                    self.events.text_delta(event.text)
                elif isinstance(event, StreamDone):
                    final = event.message
                elif isinstance(event, StreamError):
                    self.events.status(f"Main model error: {event.error}", error=True)
                    return None
        except ModelCancelled:
            self.events.status("Stopped.")
            return None
        except ModelError as exc:
            self.events.status(f"Main model error: {exc}", error=True)
            return None
        if final is None:
            self.events.status("The model returned nothing.", error=True)
            return None
        self._append(final)
        self.events.assistant_message(final)
        return final

    def _run_skills(self, reply: Message) -> None:
        assert self.session is not None
        text_mode = self.config.models.main.tool_format == "text"
        for call in reply.tool_calls:
            if self.cancel.cancelled:
                result_msg = Message(
                    "tool",
                    "Cancelled by the user before this skill ran.",
                    tool_call_id=call.id,
                    name=call.name,
                )
                self._append(
                    result_msg
                    if not text_mode
                    else text_tools.tool_result_message(call, result_msg.content)
                )
                continue
            self.events.skill_started(call.id, call.name, call.arguments)
            ctx = SkillContext(
                cwd=self.session.cwd,
                config=self.config,
                services=self.services,
                session_id=self.session.id,
                cancel=self.cancel,
            )
            outcome = self.runner.execute(call, ctx)
            result_msg = Message(
                "tool", outcome.result.content, tool_call_id=call.id, name=call.name
            )
            seq = self._append(
                result_msg
                if not text_mode
                else text_tools.tool_result_message(call, result_msg.content)
            )
            handoff = outcome.result.handoff
            self.store.record_skill_event(
                self.session.id,
                seq,
                call.id,
                call.name,
                outcome.result.ok,
                handoff.to_dict() if handoff else None,
                outcome.result.error,
            )
            if self.services.task_lists is not None:
                self.store.save_tasks(
                    self.session.id, self.services.task_lists.get(self.session.id).to_json()
                )
            self.events.skill_finished(outcome)
            if outcome.handoff_now and handoff is not None:
                self.events.handoff_requested(handoff)

    def _maybe_compact(self) -> None:
        main = self.config.models.main
        if (
            main.auto_compact
            and self.session
            and needs_compaction(
                self.session.messages, main.context_window, main.compaction_threshold
            )
        ):
            self._compact()

    def _compact(self, force: bool = False) -> bool:
        assert self.session is not None
        self.events.status("Compacting older turns...")
        try:
            compacted = compact(
                self.session.messages,
                self.model,
                keep_last=self.config.models.main.compaction_keep_last,
                cancel=self.cancel,
            )
        except ModelError as exc:
            self.events.status(f"Compaction failed: {exc}", error=True)
            return False
        if compacted is self.session.messages or len(compacted) == len(self.session.messages):
            if force:
                self.events.status("Nothing to compact yet.")
            return False
        self.session.messages = compacted
        self.store.replace_messages(self.session.id, compacted)
        summary = next(
            (m.content for m in compacted if m.role == "user" and m.content.startswith("[Summary")),
            "",
        )
        self.events.compacted(summary)
        self.events.status("Compacted.")
        return True
