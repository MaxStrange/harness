"""A scripted fake model for unit tests (P17): no server, deterministic output."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from harness.model.types import (
    CancelToken,
    Message,
    ModelCancelled,
    ModelError,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)


@dataclass
class FakeModel:
    """Replays ``script`` one entry per call.

    Each entry is either a string (a plain assistant reply) or a :class:`Message`
    (for tool calls). When the script runs out the model replies "(no more script)".
    """

    script: list[str | Message] = field(default_factory=list)
    requests: list[list[Message]] = field(default_factory=list)
    tools_seen: list[list[ToolSpec]] = field(default_factory=list)
    offline_reason: str | None = None
    name: str = "fake"
    chunk_size: int = 5

    @staticmethod
    def tool_call(name: str, call_id: str = "call_1", **arguments) -> Message:
        return Message(
            role="assistant",
            tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        )

    def _next(self) -> Message:
        if not self.script:
            return Message(role="assistant", content="(no more script)")
        entry = self.script.pop(0)
        if isinstance(entry, str):
            return Message(role="assistant", content=entry)
        return entry

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        if self.offline_reason:
            raise ModelError(self.offline_reason)
        self.requests.append(list(messages))
        self.tools_seen.append(list(tools or []))
        message = self._next()
        for i in range(0, len(message.content), self.chunk_size):
            if cancel is not None and cancel.cancelled:
                raise ModelCancelled("cancelled")
            yield TextDelta(message.content[i : i + self.chunk_size])
        yield StreamDone(message, finish_reason="tool_calls" if message.tool_calls else "stop")

    def complete(
        self,
        messages: list[Message],
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Message:
        final = None
        for event in self.stream_chat(messages, cancel=cancel):
            if isinstance(event, StreamDone):
                final = event.message
        assert final is not None
        return final

    def health(self) -> str | None:
        return self.offline_reason
