"""Plain data types shared by the model client, the agent loop and the store."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


class CancelToken:
    """Thread-safe cancellation flag shared between the UI and worker threads (P3)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def reset(self) -> None:
        self._event.clear()


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str | None = None

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or json.dumps(self.arguments),
            },
        }


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_api(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
            if not self.content:
                data["content"] = None
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data

    def to_json(self) -> str:
        return json.dumps(
            {
                "role": self.role,
                "content": self.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "raw_arguments": tc.raw_arguments,
                    }
                    for tc in self.tool_calls
                ],
                "tool_call_id": self.tool_call_id,
                "name": self.name,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> Message:
        data = json.loads(text)
        return cls(
            role=data["role"],
            content=data.get("content") or "",
            tool_calls=[
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc.get("arguments") or {},
                    raw_arguments=tc.get("raw_arguments") or "",
                )
                for tc in data.get("tool_calls") or []
            ],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )


@dataclass
class ToolSpec:
    """What the model sees for one skill."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# Streaming events -----------------------------------------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class StreamDone:
    message: Message
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class StreamError:
    error: str


StreamEvent = TextDelta | StreamDone | StreamError


class ModelError(Exception):
    """A model call failed (network, HTTP status, malformed response)."""


class ModelCancelled(ModelError):
    """The call was cancelled through its :class:`CancelToken`."""


def estimate_tokens(text: str) -> int:
    """Cheap token estimate used for compaction decisions (about 4 chars per token)."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[Message]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.content) + 4
        for tc in msg.tool_calls:
            total += estimate_tokens(tc.raw_arguments or json.dumps(tc.arguments)) + 8
    return total
