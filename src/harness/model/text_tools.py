"""Text tool-call format for models without native function calling (P1).

The model is told to emit tool calls as fenced blocks::

    ```tool_call
    {"name": "read_file", "arguments": {"path": "README.md"}}
    ```

This module renders the instructions for that format and parses the blocks
back out of a completed response.
"""

from __future__ import annotations

import json
import re
import uuid

from harness.model.types import Message, ToolCall, ToolSpec

FENCE = "```tool_call"
_BLOCK_RE = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)


def format_instructions(tools: list[ToolSpec]) -> str:
    lines = [
        "You have the following tools. To call one, write a fenced block exactly like this,",
        "with valid JSON inside, and nothing else after it in the same message:",
        "",
        FENCE,
        '{"name": "<tool name>", "arguments": {<arguments>}}',
        "```",
        "",
        "You may call several tools by writing several blocks. Tool results arrive in the",
        "next message. Tools:",
        "",
    ]
    for tool in tools:
        lines.append(f"### {tool.name}")
        lines.append(tool.description.strip())
        lines.append("Parameters (JSON schema):")
        lines.append(json.dumps(tool.parameters))
        lines.append("")
    return "\n".join(lines)


def parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Split a response into (visible text, tool calls)."""
    calls: list[ToolCall] = []
    for match in _BLOCK_RE.finditer(text):
        body = match.group(1).strip()
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        try:
            data = json.loads(body)
            name = str(data.get("name", ""))
            args = data.get("arguments", data.get("parameters", {})) or {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            calls.append(ToolCall(id=call_id, name=name, arguments=args, raw_arguments=body))
        except (ValueError, AttributeError) as exc:
            calls.append(
                ToolCall(
                    id=call_id,
                    name="",
                    raw_arguments=body,
                    parse_error=f"could not parse tool call: {exc}",
                )
            )
    visible = _BLOCK_RE.sub("", text).strip()
    return visible, calls


def tool_result_message(call: ToolCall, content: str) -> Message:
    """In text mode tool results go back as user messages the model can read."""
    return Message(
        role="user",
        content=f"[Result of {call.name} (call {call.id})]\n{content}",
    )
