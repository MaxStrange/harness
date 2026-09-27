"""OpenAI-compatible chat client for llama.cpp's server (M3), with streaming (P2).

Kept deliberately small and dependency-light: one HTTP call per request over
``httpx``, server-sent events parsed by hand, tool-call deltas assembled by index.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any, Protocol

import httpx

from harness.config import Endpoint
from harness.logging_setup import prompts_log
from harness.model import text_tools
from harness.model.types import (
    CancelToken,
    Message,
    ModelCancelled,
    ModelError,
    StreamDone,
    StreamError,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger(__name__)


class ChatModel(Protocol):
    """What the agent loop and the small agents need from any model."""

    name: str

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]: ...

    def complete(
        self,
        messages: list[Message],
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Message: ...

    def health(self) -> str | None:
        """None when reachable, otherwise a short reason."""
        ...


class OpenAICompatClient:
    """Talks to one endpoint. ``tool_format`` chooses native function calling or text."""

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        tool_format: str = "native",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.tool_format = tool_format
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.name = f"{endpoint.model}@{endpoint.base_url}"
        headers = {"Content-Type": "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        self._client = httpx.Client(
            base_url=endpoint.base_url,
            headers=headers,
            timeout=httpx.Timeout(endpoint.timeout_s, connect=min(10.0, endpoint.timeout_s)),
            transport=transport,
            trust_env=False,
        )

    # -- requests ---------------------------------------------------------

    def _body(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        api_messages = [m.to_api() for m in messages]
        body: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": api_messages,
            "stream": stream,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if tools:
            if self.tool_format == "native":
                body["tools"] = [t.to_api() for t in tools]
                body["tool_choice"] = "auto"
            else:
                # Text mode: the tool instructions ride along in the system prompt.
                instructions = text_tools.format_instructions(tools)
                if api_messages and api_messages[0]["role"] == "system":
                    api_messages[0] = dict(api_messages[0])
                    api_messages[0]["content"] = f"{api_messages[0]['content']}\n\n{instructions}"
                else:
                    api_messages.insert(0, {"role": "system", "content": instructions})
                body["messages"] = api_messages
        return body

    def health(self) -> str | None:
        try:
            response = self._client.get("/models", timeout=5.0)
        except httpx.HTTPError as exc:
            return f"{self.endpoint.base_url}: {exc.__class__.__name__}: {exc}"
        if response.status_code >= 400:
            return f"{self.endpoint.base_url}: HTTP {response.status_code}"
        return None

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
            raise ModelError("stream ended without a final message")
        return final

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        cancel: CancelToken | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        body = self._body(messages, tools, True, temperature, max_tokens)
        plog = prompts_log()
        plog.info("REQUEST %s\n%s", self.name, json.dumps(body, indent=1, ensure_ascii=False))
        started = time.monotonic()
        try:
            yield from self._stream(body, tools, cancel)
        except ModelCancelled:
            plog.info("CANCELLED %s after %.1fs", self.name, time.monotonic() - started)
            raise
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:2000]
            plog.error("HTTP %s from %s: %s", exc.response.status_code, self.name, detail)
            yield StreamError(f"HTTP {exc.response.status_code} from {self.name}: {detail}")
        except httpx.HTTPError as exc:
            plog.error("transport error from %s: %s", self.name, exc)
            yield StreamError(f"{exc.__class__.__name__} talking to {self.name}: {exc}")

    def _stream(
        self, body: dict[str, Any], tools: list[ToolSpec] | None, cancel: CancelToken | None
    ) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        pending: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        prompt_tokens = completion_tokens = None
        gate = _FenceGate() if (tools and self.tool_format == "text") else None

        with self._client.stream("POST", "/chat/completions", json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancel is not None and cancel.cancelled:
                    response.close()
                    raise ModelCancelled("cancelled by user")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("unparseable SSE chunk: %r", payload[:200])
                    continue
                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                for choice in chunk.get("choices", []):
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        text_parts.append(content)
                        if gate is not None:
                            visible = gate.feed(content)
                            if visible:
                                yield TextDelta(visible)
                        else:
                            yield TextDelta(content)
                    for tc in delta.get("tool_calls") or []:
                        index = tc.get("index", 0)
                        slot = pending.setdefault(index, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        function = tc.get("function") or {}
                        if function.get("name"):
                            slot["name"] += function["name"]
                        if function.get("arguments"):
                            slot["args"] += function["arguments"]

        full_text = "".join(text_parts)
        if gate is not None:
            tail = gate.flush()
            if tail:
                yield TextDelta(tail)
        tool_calls = [_finish_tool_call(i, slot) for i, slot in sorted(pending.items())]
        if tools and self.tool_format == "text":
            full_text, parsed = text_tools.parse_tool_calls(full_text)
            tool_calls.extend(parsed)
        message = Message(role="assistant", content=full_text, tool_calls=tool_calls)
        prompts_log().info("RESPONSE %s\n%s", self.name, message.to_json())
        yield StreamDone(
            message,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def close(self) -> None:
        self._client.close()


def _finish_tool_call(index: int, slot: dict[str, str]) -> ToolCall:
    call_id = slot["id"] or f"call_{index}"
    raw = slot["args"]
    try:
        args = json.loads(raw) if raw.strip() else {}
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
        return ToolCall(id=call_id, name=slot["name"], arguments=args, raw_arguments=raw)
    except ValueError as exc:
        return ToolCall(
            id=call_id,
            name=slot["name"],
            raw_arguments=raw,
            parse_error=f"arguments are not valid JSON: {exc}",
        )


class _FenceGate:
    """Holds back streamed text that might be the start of a ```tool_call block.

    In text tool mode the block should not flash up in the chat; everything
    else passes straight through.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_block = False

    def feed(self, text: str) -> str:
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            if self._in_block:
                end = self._buffer.find("\n```", 1)
                if end == -1:
                    break
                self._buffer = self._buffer[end + 4 :]
                self._in_block = False
                continue
            start = self._buffer.find("```")
            if start == -1:
                # Hold back a trailing partial fence ("`" or "``") until more arrives.
                keep = 2 if self._buffer.endswith("``") else 1 if self._buffer.endswith("`") else 0
                cut = len(self._buffer) - keep
                out.append(self._buffer[:cut])
                self._buffer = self._buffer[cut:]
                break
            out.append(self._buffer[:start])
            rest = self._buffer[start:]
            if len(rest) < len(text_tools.FENCE):
                if text_tools.FENCE.startswith(rest):
                    self._buffer = rest  # not enough to decide yet
                    break
            elif rest.startswith(text_tools.FENCE):
                self._in_block = True
                self._buffer = rest
                continue
            # An ordinary code fence: pass the fence through and keep going.
            out.append("```")
            self._buffer = rest[3:]
        return "".join(out)

    def flush(self) -> str:
        if self._in_block:
            self._buffer = ""
            return ""
        out, self._buffer = self._buffer, ""
        return out
