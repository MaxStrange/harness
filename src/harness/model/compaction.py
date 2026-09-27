"""Context compaction (P4): summarize older turns when a session outgrows the window."""

from __future__ import annotations

import logging

from harness.model.client import ChatModel
from harness.model.types import CancelToken, Message, ModelError, estimate_messages_tokens

log = logging.getLogger(__name__)

SUMMARY_PREFIX = "[Summary of the earlier part of this conversation]\n"

SUMMARY_PROMPT = (
    "Summarize the conversation so far for an AI assistant that will continue it. Keep: "
    "the user's goals and constraints, decisions made, facts discovered, file paths, "
    "commands run and their outcomes, and any unfinished work. Be dense and factual; "
    "use bullet points. Do not add commentary."
)


def needs_compaction(messages: list[Message], context_window: int, threshold: float) -> bool:
    return estimate_messages_tokens(messages) > context_window * threshold


def split_for_compaction(
    messages: list[Message], keep_last: int = 6
) -> tuple[list[Message], list[Message], list[Message]]:
    """Return (system messages, older turns to summarize, recent turns to keep).

    The cut never separates an assistant tool call from its tool results.
    """
    system = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]
    if len(rest) <= keep_last:
        return system, [], rest
    cut = len(rest) - keep_last
    while cut > 0 and rest[cut].role == "tool":
        cut -= 1
    return system, rest[:cut], rest[cut:]


def render_transcript(messages: list[Message]) -> str:
    lines = []
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            calls = "; ".join(
                f"{tc.name}({tc.raw_arguments or tc.arguments})" for tc in msg.tool_calls
            )
            lines.append(f"ASSISTANT calls: {calls}")
        if msg.content:
            lines.append(f"{msg.role.upper()}: {msg.content}")
    return "\n".join(lines)


def compact(
    messages: list[Message],
    model: ChatModel,
    *,
    keep_last: int = 6,
    cancel: CancelToken | None = None,
) -> list[Message]:
    """Replace older turns with one summary message. Raises ModelError if the model fails."""
    system, older, recent = split_for_compaction(messages, keep_last)
    if not older:
        return messages
    transcript = render_transcript(older)
    prompt = [
        Message(role="system", content=SUMMARY_PROMPT),
        Message(role="user", content=transcript),
    ]
    try:
        reply = model.complete(prompt, cancel=cancel)
    except ModelError:
        log.exception("compaction failed")
        raise
    summary = Message(role="user", content=SUMMARY_PREFIX + reply.content.strip())
    # If the recent window starts with tool results, their calls were summarized;
    # drop the orphaned results so the API never sees a tool message without a call.
    while recent and recent[0].role == "tool":
        recent.pop(0)
    return [*system, summary, *recent]
