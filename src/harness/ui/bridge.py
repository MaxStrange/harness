"""Thread bridges between the agent/skill worker threads and the GUI thread (P13).

* :class:`AgentSignals` implements :class:`~harness.agent.loop.AgentEvents` by emitting Qt signals.
* :class:`QtUiBridge` implements the skills' :class:`~harness.skills.base.UiBridge`; its methods are
  called on worker threads and hop to the GUI thread through signals (blocking where a result is needed).
* :class:`AgentController` runs turns on a background thread and wires the approval broker.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

from harness.agent.loop import Agent
from harness.model.types import Message
from harness.skills.base import Handoff
from harness.skills.runner import ApprovalBroker, PendingApproval, SkillOutcome

log = logging.getLogger(__name__)


class AgentSignals(QObject):
    turn_started = Signal()
    text_delta = Signal(str)
    assistant_message = Signal(object)
    skill_started = Signal(str, str, object)
    approval_needed = Signal(object)
    skill_finished = Signal(object)
    handoff_requested = Signal(object)
    status = Signal(str, bool)
    compacted = Signal(str)
    turn_finished = Signal(bool)


class _EventsAdapter:
    """The AgentEvents protocol, forwarding to :class:`AgentSignals`."""

    def __init__(self, signals: AgentSignals) -> None:
        self.s = signals

    def turn_started(self) -> None:
        self.s.turn_started.emit()

    def text_delta(self, text: str) -> None:
        self.s.text_delta.emit(text)

    def assistant_message(self, message: Message) -> None:
        self.s.assistant_message.emit(message)

    def skill_started(self, call_id: str, skill: str, args: dict) -> None:
        self.s.skill_started.emit(call_id, skill, args)

    def approval_needed(self, pending: PendingApproval) -> None:
        self.s.approval_needed.emit(pending)

    def skill_finished(self, outcome: SkillOutcome) -> None:
        self.s.skill_finished.emit(outcome)

    def handoff_requested(self, handoff: Handoff) -> None:
        self.s.handoff_requested.emit(handoff)

    def status(self, text: str, error: bool = False) -> None:
        self.s.status.emit(text, error)

    def compacted(self, summary: str) -> None:
        self.s.compacted.emit(summary)

    def turn_finished(self, cancelled: bool) -> None:
        self.s.turn_finished.emit(cancelled)


class MainThreadInvoker(QObject):
    """Run a callable on the GUI thread from any thread, optionally waiting for its result."""

    _invoke = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job: tuple[Callable[[], Any], concurrent.futures.Future]) -> None:
        fn, future = job
        try:
            future.set_result(fn())
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)

    def call(self, fn: Callable[[], Any], timeout: float | None = 15.0) -> Any:
        future: concurrent.futures.Future = concurrent.futures.Future()
        if threading.current_thread() is threading.main_thread():
            self._run((fn, future))
        else:
            self._invoke.emit((fn, future))
        return future.result(timeout=timeout)

    def post(self, fn: Callable[[], Any]) -> None:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._invoke.emit((fn, future))


class QtUiBridge(QObject):
    """The skills' window into the GUI. The main window connects to these signals."""

    notification = Signal(str, str)
    clipboard = Signal(str)
    embedded_requested = Signal(object)

    def __init__(self, open_external: Callable[[Handoff], str | None]) -> None:
        super().__init__()
        self._open_external = open_external
        self.invoker = MainThreadInvoker()

    def notify(self, title: str, body: str) -> None:
        self.notification.emit(title, body)

    def set_clipboard(self, text: str) -> None:
        self.clipboard.emit(text)

    def open_external(self, handoff: Handoff) -> str | None:
        try:
            return self.invoker.call(lambda: self._open_external(handoff))
        except concurrent.futures.TimeoutError:
            return "the user interface did not respond in time"
        except Exception as exc:  # noqa: BLE001
            return f"{exc.__class__.__name__}: {exc}"

    def open_embedded(self, handoff: Handoff) -> None:
        self.embedded_requested.emit(handoff)


class AgentController(QObject):
    """Owns the agent, runs its turns off the GUI thread, and routes approvals to the UI."""

    def __init__(
        self, agent: Agent, broker: ApprovalBroker, signals: AgentSignals | None = None
    ) -> None:
        super().__init__()
        self.agent = agent
        self.broker = broker
        self.signals = signals or AgentSignals()
        self.agent.events = _EventsAdapter(self.signals)
        self.broker.handler = self._on_approval
        self._thread: threading.Thread | None = None

    def _on_approval(self, pending: PendingApproval) -> None:
        # Called on the skill thread; the UI shows the request and later calls broker.resolve().
        self.signals.approval_needed.emit(pending)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def send(self, text: str) -> bool:
        if self.busy:
            return False
        self._thread = threading.Thread(
            target=self.agent.send, args=(text,), name="agent-turn", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self.agent.stop()

    def compact(self) -> None:
        if self.busy:
            self.signals.status.emit("Wait for the current turn to finish before compacting.", True)
            return
        threading.Thread(target=self.agent.compact_now, name="compact", daemon=True).start()
