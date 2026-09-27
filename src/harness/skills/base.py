"""The skill contract (F1-F3, P6).

A skill is one Python class. It declares what the model sees (name,
description, JSON-schema parameters), whether it needs approval, how long it
may run, and how its result is handed to the user. The harness does the rest:
registering it, presenting it to the model, running it with a timeout,
showing its result, logging it, and offering the handoff.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from harness.model.types import CancelToken
from harness.security.paths import resolve_path

if TYPE_CHECKING:
    from harness.config import Config
    from harness.model.web_reader import WebReader
    from harness.security.net import NetPolicy
    from harness.security.paths import PathPolicy
    from harness.web.fetch import SafeFetcher
    from harness.web.searxng import SearxClient

HandoffKind = Literal["external", "embedded", "done"]

# External actions the UI knows how to perform.
EXTERNAL_ACTIONS = ("editor", "file_manager", "browser", "default_app")
# Embedded views the UI provides (UI5). Adding a view means adding it here and in the UI.
EMBEDDED_VIEWS = ("terminal", "image", "tasks")


@dataclass
class Handoff:
    """How the user takes over from where the model left off (H1, H2).

    ``kind``:
      * ``external`` - open ``target`` in an outside application (``action`` is one of
        :data:`EXTERNAL_ACTIONS`).
      * ``embedded`` - show a view inside the harness (``action`` is one of
        :data:`EMBEDDED_VIEWS`; ``args`` configures it, e.g. the terminal session).
      * ``done`` - the skill itself was the handoff (clipboard, notification, open-with).
    """

    kind: HandoffKind
    action: str
    label: str
    target: str | None = None
    line: int | None = None
    args: dict[str, Any] = field(default_factory=dict)

    # Convenience constructors keep skills short and consistent.
    @classmethod
    def editor(
        cls, path: str | Path, line: int | None = None, label: str = "Open in editor"
    ) -> Handoff:
        return cls("external", "editor", label, target=str(path), line=line)

    @classmethod
    def file_manager(cls, path: str | Path, label: str = "Show in file manager") -> Handoff:
        return cls("external", "file_manager", label, target=str(path))

    @classmethod
    def browser(cls, url: str, label: str = "Open in browser") -> Handoff:
        return cls("external", "browser", label, target=url)

    @classmethod
    def default_app(cls, target: str | Path, label: str = "Open") -> Handoff:
        return cls("external", "default_app", label, target=str(target))

    @classmethod
    def terminal(
        cls, cwd: str | Path, session: str = "main", label: str = "Open terminal"
    ) -> Handoff:
        return cls("embedded", "terminal", label, target=str(cwd), args={"session": session})

    @classmethod
    def image(cls, path: str | Path, label: str = "View image") -> Handoff:
        return cls("embedded", "image", label, target=str(path))

    @classmethod
    def tasks(cls, label: str = "Show task list") -> Handoff:
        return cls("embedded", "tasks", label)

    @classmethod
    def done(cls, label: str) -> Handoff:
        return cls("done", "none", label)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "label": self.label,
            "target": self.target,
            "line": self.line,
            "args": self.args,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Handoff:
        return cls(
            data["kind"],
            data["action"],
            data["label"],
            data.get("target"),
            data.get("line"),
            data.get("args") or {},
        )


@dataclass
class SkillResult:
    """What a skill returns. ``content`` goes to the model; everything is shown to the user."""

    content: str
    handoff: Handoff | None = None
    ok: bool = True
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, error: str, handoff: Handoff | None = None) -> SkillResult:
        return cls(content=f"Error: {error}", handoff=handoff, ok=False, error=error)


DetailKind = Literal["command", "diff", "text"]


@dataclass
class ApprovalRequest:
    """Shown inline in the chat (P8). ``detail`` is the exact thing that will happen."""

    skill: str
    title: str
    detail: str
    detail_kind: DetailKind = "text"
    reason: str | None = None
    editable_field: str | None = None
    summary: str | None = None  # filled in by the runner from the summarizer
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalDecision:
    approved: bool
    edited_args: dict[str, Any] | None = None
    reason: str | None = None

    @classmethod
    def approve(cls, edited_args: dict[str, Any] | None = None) -> ApprovalDecision:
        return cls(True, edited_args)

    @classmethod
    def reject(cls, reason: str | None = None) -> ApprovalDecision:
        return cls(False, None, reason)


class UiBridge(Protocol):
    """Thread-safe calls a skill may make into the GUI. The Qt app implements this;
    tests use :class:`RecordingUi`."""

    def notify(self, title: str, body: str) -> None: ...

    def set_clipboard(self, text: str) -> None: ...

    def open_external(self, handoff: Handoff) -> str | None:
        """Perform an external handoff now. Returns an error string or None."""
        ...

    def open_embedded(self, handoff: Handoff) -> None: ...

    def set_preference(self, name: str, value: str) -> str | None:
        """Change a live UI preference (e.g. the critter). Returns an error string or None."""
        ...


class RecordingUi:
    """A :class:`UiBridge` that records calls, for tests and headless use."""

    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []
        self.clipboard: list[str] = []
        self.opened: list[Handoff] = []

    def notify(self, title: str, body: str) -> None:
        self.notifications.append((title, body))

    def set_clipboard(self, text: str) -> None:
        self.clipboard.append(text)

    def open_external(self, handoff: Handoff) -> str | None:
        self.opened.append(handoff)
        return None

    def open_embedded(self, handoff: Handoff) -> None:
        self.opened.append(handoff)

    def set_preference(self, name: str, value: str) -> str | None:
        self.preferences = getattr(self, "preferences", {})
        self.preferences[name] = value
        return None


@dataclass
class Services:
    """Everything a skill may need beyond its arguments. Members are optional so
    tests construct only what a skill uses; a skill that needs a missing service
    returns a clear error."""

    path_policy: PathPolicy | None = None
    net_policy: NetPolicy | None = None
    fetcher: SafeFetcher | None = None
    searx: SearxClient | None = None
    web_reader: WebReader | None = None
    terminals: Any | None = None  # harness.terminal.manager.TerminalManager
    ui: UiBridge = field(default_factory=RecordingUi)
    task_lists: Any | None = None  # harness.skills.tasklist_store.TaskListStore
    background_jobs: Any | None = None  # harness.terminal.manager.BackgroundJobs


@dataclass
class SkillContext:
    """Per-call context (P12): the session's working directory, config and services."""

    cwd: Path
    config: Config
    services: Services
    session_id: str = "test"
    cancel: CancelToken = field(default_factory=CancelToken)

    def resolve(self, path: str) -> Path:
        return resolve_path(path, self.cwd)

    def need(self, name: str) -> Any:
        """Fetch a service or raise a clear error."""
        service = getattr(self.services, name, None)
        if service is None:
            raise SkillUnavailable(f"the {name} service is not available in this harness")
        return service

    def check_cancelled(self) -> None:
        if self.cancel.cancelled:
            raise SkillCancelled("cancelled by user")


class SkillError(Exception):
    """A skill failed in an expected way; the message goes to the model and the user."""


class SkillUnavailable(SkillError):
    pass


class SkillCancelled(SkillError):
    pass


class Skill(ABC):
    """Base class every skill subclasses. Class attributes are the declaration (P6)."""

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]
    needs_approval: ClassVar[bool] = False
    timeout_s: ClassVar[float | None] = None
    handoff_description: ClassVar[str]

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        """Do the work. Raise :class:`SkillError` for expected failures."""

    def approval_request(self, args: dict[str, Any], ctx: SkillContext) -> ApprovalRequest | None:
        """Return a request if this call needs approval, else None.

        The default honours ``needs_approval``; skills override to make the decision
        per call (for example a read of a protected path, P9).
        """
        if not self.needs_approval:
            return None
        return ApprovalRequest(
            skill=self.name,
            title=f"Run skill {self.name}",
            detail=json.dumps(args, indent=2, ensure_ascii=False),
            detail_kind="text",
            args=args,
        )

    def protected_path_request(
        self, path: Path, args: dict[str, Any], ctx: SkillContext, action: str = "read"
    ) -> ApprovalRequest | None:
        """Helper for file skills: approval when ``path`` is on the deny list (P9)."""
        policy = ctx.services.path_policy
        if policy is None:
            return None
        reason = policy.denial_reason(path)
        if reason is None:
            return None
        return ApprovalRequest(
            skill=self.name,
            title=f"Allow {self.name} to {action} a protected path?",
            detail=str(path),
            detail_kind="text",
            reason=reason,
            args=args,
        )

    def summarize_for_approval(self, request: ApprovalRequest) -> bool:
        """Whether the runner should ask the summarizer to explain ``request.detail``."""
        return request.detail_kind == "command"
