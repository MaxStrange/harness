"""Running skills: validation, approval, timeout, error capture and the audit log (P7, P8, SEC1)."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from harness.logging_setup import audit_log
from harness.model.summarizer import Summarizer
from harness.model.types import CancelToken, ToolCall
from harness.skills.base import (
    ApprovalDecision,
    ApprovalRequest,
    Skill,
    SkillCancelled,
    SkillContext,
    SkillError,
    SkillResult,
)
from harness.skills.registry import HANDOFF_PARAM, SkillRegistry
from harness.skills.schema import validate_args

log = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    id: str
    request: ApprovalRequest
    decision: ApprovalDecision | None = None
    event: threading.Event = field(default_factory=threading.Event)
    summary_pending: bool = False
    summary_listeners: list[Callable[[str], None]] = field(default_factory=list)

    def set_summary(self, text: str) -> None:
        """Called from the summarizer thread once the plain-language summary is ready."""
        self.request.summary = text
        self.summary_pending = False
        for listener in list(self.summary_listeners):
            try:
                listener(text)
            except Exception:  # noqa: BLE001
                log.exception("summary listener failed")


ApprovalHandler = Callable[[PendingApproval], None]


class ApprovalBroker:
    """Hands approval requests from worker threads to the UI and waits for the answer.

    The UI installs a ``handler`` that is called (on the worker thread) with a
    :class:`PendingApproval`; it must eventually call :meth:`resolve`. Without a
    handler every request is rejected, so nothing runs unattended.
    """

    def __init__(self, handler: ApprovalHandler | None = None) -> None:
        self.handler = handler
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def request(
        self, request: ApprovalRequest, cancel: CancelToken | None = None
    ) -> ApprovalDecision:
        pending = PendingApproval(id=uuid.uuid4().hex, request=request)
        with self._lock:
            self._pending[pending.id] = pending
        try:
            if self.handler is None:
                return ApprovalDecision.reject("no approval handler installed")
            self.handler(pending)
            while not pending.event.wait(0.1):
                if cancel is not None and cancel.cancelled:
                    return ApprovalDecision.reject("cancelled by user")
            return pending.decision or ApprovalDecision.reject("no decision")
        finally:
            with self._lock:
                self._pending.pop(pending.id, None)

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> bool:
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return False
        pending.decision = decision
        pending.event.set()
        return True

    def pending(self) -> list[PendingApproval]:
        with self._lock:
            return list(self._pending.values())


def auto_approve_broker() -> ApprovalBroker:
    """For tests and stack checks: approves everything."""
    broker = ApprovalBroker()
    broker.handler = lambda p: broker.resolve(p.id, ApprovalDecision.approve())
    return broker


@dataclass
class SkillOutcome:
    """What the agent loop gets back: the result plus what happened around it."""

    call: ToolCall
    result: SkillResult
    skill: Skill | None
    approval: ApprovalRequest | None = None
    decision: ApprovalDecision | None = None
    args: dict = field(default_factory=dict)
    handoff_now: bool = False


class SkillRunner:
    def __init__(
        self,
        registry: SkillRegistry,
        broker: ApprovalBroker,
        summarizer: Summarizer | None = None,
        *,
        default_timeout_s: float = 60,
        timeouts: dict[str, float] | None = None,
        max_result_chars: int = 30000,
    ) -> None:
        self.registry = registry
        self.broker = broker
        self.summarizer = summarizer
        self.default_timeout_s = default_timeout_s
        self.timeouts = timeouts or {}
        self.max_result_chars = max_result_chars
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="skill"
        )

    def timeout_for(self, skill: Skill) -> float:
        if skill.name in self.timeouts:
            return self.timeouts[skill.name]
        if skill.timeout_s is not None:
            return skill.timeout_s
        return self.default_timeout_s

    def execute(self, call: ToolCall, ctx: SkillContext) -> SkillOutcome:
        audit = audit_log()
        skill = self.registry.get(call.name)
        if call.parse_error:
            audit.warning(
                "REJECTED %s: %s (raw=%r)",
                call.name or "?",
                call.parse_error,
                call.raw_arguments[:500],
            )
            return SkillOutcome(
                call,
                SkillResult.fail(
                    f"{call.parse_error}. Raw arguments were: {call.raw_arguments[:2000]}"
                ),
                skill,
            )
        if skill is None:
            known = ", ".join(self.registry.names())
            audit.warning("UNKNOWN skill %s", call.name)
            return SkillOutcome(
                call,
                SkillResult.fail(f"unknown skill {call.name!r}. Available skills: {known}"),
                None,
            )

        args = dict(call.arguments)
        handoff_now = bool(args.pop(HANDOFF_PARAM, False))
        problems = validate_args(skill.parameters, args)
        if problems:
            audit.warning("INVALID %s %s: %s", skill.name, json.dumps(args)[:500], problems)
            return SkillOutcome(
                call,
                SkillResult.fail("invalid arguments: " + "; ".join(problems)),
                skill,
                args=args,
                handoff_now=handoff_now,
            )

        audit.info(
            "CALL %s %s (cwd=%s)", skill.name, json.dumps(args, ensure_ascii=False)[:5000], ctx.cwd
        )

        # -- approval ------------------------------------------------------
        approval: ApprovalRequest | None = None
        decision: ApprovalDecision | None = None
        try:
            approval = skill.approval_request(args, ctx)
        except SkillError as exc:
            return SkillOutcome(call, SkillResult.fail(str(exc)), skill, args=args)
        if approval is not None:
            if self.summarizer is not None and skill.summarize_for_approval(approval):
                approval.summary = self.summarizer.summarize(
                    approval.detail, cancel=ctx.cancel
                ).display
            decision = self.broker.request(approval, ctx.cancel)
            if not decision.approved:
                audit.info("DENIED %s: %s", skill.name, decision.reason or "no reason given")
                reason = f" Reason: {decision.reason}" if decision.reason else ""
                return SkillOutcome(
                    call,
                    SkillResult(
                        content=f"The user rejected this {skill.name} call.{reason}",
                        ok=False,
                        error="rejected by user",
                    ),
                    skill,
                    approval,
                    decision,
                    args,
                )
            if decision.edited_args:
                args = {**args, **decision.edited_args}
                audit.info(
                    "APPROVED (edited) %s %s",
                    skill.name,
                    json.dumps(args, ensure_ascii=False)[:5000],
                )
            else:
                audit.info("APPROVED %s", skill.name)

        # -- run with timeout ---------------------------------------------
        timeout = self.timeout_for(skill)
        future = self._pool.submit(self._run_guarded, skill, args, ctx)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            ctx.cancel.cancel()
            message = f"{skill.name} timed out after {timeout:g}s (skills.timeouts.{skill.name} in the config changes this)"
            audit.error("TIMEOUT %s after %gs", skill.name, timeout)
            result = SkillResult.fail(message)

        if len(result.content) > self.max_result_chars:
            audit.info(
                "RESULT %s truncated for the model from %d chars", skill.name, len(result.content)
            )
            result.data["full_content"] = result.content
            result.content = (
                result.content[: self.max_result_chars]
                + f"\n...[truncated {len(result.content) - self.max_result_chars} characters; the full output is in the audit log]"
            )
        audit.info(
            "RESULT %s ok=%s\n%s",
            skill.name,
            result.ok,
            result.data.get("full_content", result.content),
        )
        return SkillOutcome(call, result, skill, approval, decision, args, handoff_now)

    @staticmethod
    def _run_guarded(skill: Skill, args: dict, ctx: SkillContext) -> SkillResult:
        try:
            return skill.run(args, ctx)
        except SkillCancelled as exc:
            return SkillResult.fail(f"{skill.name} was cancelled: {exc}")
        except SkillError as exc:
            log.info("skill %s failed: %s", skill.name, exc)
            return SkillResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - never crash the harness (P7)
            text = traceback.format_exc()
            log.error("skill %s crashed:\n%s", skill.name, text)
            audit_log().error("CRASH %s\n%s", skill.name, text)
            return SkillResult.fail(
                f"{skill.name} crashed with {exc.__class__.__name__}: {exc}\n{text}"
            )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
