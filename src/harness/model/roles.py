"""Model roles (M2, M5): each role has an ordered list of endpoints tried in turn.

Which model runs where is tuned by experiment, so nothing here assumes a role
lives on a particular machine. A role is "offline" when every endpoint fails;
the reason is kept so the UI can show it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from harness.config import Endpoint
from harness.model.client import ChatModel, OpenAICompatClient
from harness.model.types import (
    CancelToken,
    Message,
    ModelCancelled,
    ModelError,
    StreamDone,
    StreamError,
    StreamEvent,
    ToolSpec,
)

log = logging.getLogger(__name__)


class RoleClient:
    """A :class:`ChatModel` that fails over between endpoints."""

    def __init__(self, role: str, clients: list[ChatModel]) -> None:
        if not clients:
            raise ValueError(f"role {role!r} needs at least one endpoint")
        self.role = role
        self.clients = clients
        self.name = f"{role}[{', '.join(c.name for c in clients)}]"
        self.last_error: str | None = None
        self.active: ChatModel | None = None

    @classmethod
    def from_endpoints(
        cls,
        role: str,
        endpoints: list[Endpoint],
        *,
        tool_format: str = "native",
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> RoleClient:
        clients = [
            OpenAICompatClient(
                ep, tool_format=tool_format, temperature=temperature, max_tokens=max_tokens
            )
            for ep in endpoints
        ]
        return cls(role, clients)

    def health(self) -> str | None:
        reasons = []
        for client in self.clients:
            reason = client.health()
            if reason is None:
                self.active = client
                self.last_error = None
                return None
            reasons.append(reason)
        self.last_error = "; ".join(reasons)
        return self.last_error

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        errors: list[str] = []
        for client in self.clients:
            produced_output = False
            try:
                for event in client.stream_chat(
                    messages, tools, cancel=cancel, temperature=temperature, max_tokens=max_tokens
                ):
                    if isinstance(event, StreamError) and not produced_output:
                        errors.append(event.error)
                        break
                    produced_output = True
                    if isinstance(event, StreamDone):
                        self.active = client
                        self.last_error = None
                    yield event
                else:
                    return
                if produced_output:
                    return
            except ModelCancelled:
                raise
            except ModelError as exc:
                if produced_output:
                    raise
                errors.append(str(exc))
            log.warning("%s: endpoint %s failed: %s", self.role, client.name, errors[-1])
        self.last_error = "; ".join(errors) or "no endpoints"
        yield StreamError(self.last_error)

    def complete(
        self,
        messages: list[Message],
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Message:
        final: Message | None = None
        for event in self.stream_chat(
            messages, None, cancel=cancel, temperature=temperature, max_tokens=max_tokens
        ):
            if isinstance(event, StreamDone):
                final = event.message
            elif isinstance(event, StreamError):
                raise ModelError(event.error)
        if final is None:
            raise ModelError("no response")
        return final
