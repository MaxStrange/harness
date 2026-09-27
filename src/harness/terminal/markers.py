"""Shell-integration markers (P11a).

The shell init scripts emit ``ESC ] 7331 ; S BEL`` right before a command runs
and ``ESC ] 7331 ; E ; <exit code> BEL`` right before the next prompt. The
parser below strips them from the byte stream (so nothing shows in xterm.js,
which would ignore them anyway) and reports them as events.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

ESC = b"\x1b"
BEL = b"\x07"
OSC_PREFIX = b"\x1b]7331;"
_MARKER_RE = re.compile(rb"\x1b\]7331;(S|E(?:;(-?\d+))?)(?:\x07|\x1b\\)")
_MAX_MARKER_LEN = 32


@dataclass
class MarkerEvent:
    kind: str  # "S" or "E"
    exit_code: int | None = None


Segment = bytes | MarkerEvent


class MarkerParser:
    """Incremental parser: feed bytes, get back the stream as ordered segments of
    cleaned bytes and :class:`MarkerEvent` objects."""

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, data: bytes) -> list[Segment]:
        buf = self._tail + data
        segments: list[Segment] = []
        pos = 0
        for match in _MARKER_RE.finditer(buf):
            if match.start() > pos:
                segments.append(buf[pos : match.start()])
            kind = match.group(1)[:1].decode()
            code = match.group(2)
            segments.append(MarkerEvent(kind, int(code) if code is not None else None))
            pos = match.end()
        rest = buf[pos:]
        # Hold back a trailing fragment that could be the start of a marker.
        hold = _partial_marker_length(rest)
        if hold:
            if len(rest) > hold:
                segments.append(rest[:-hold])
            self._tail = rest[-hold:]
        else:
            if rest:
                segments.append(rest)
            self._tail = b""
        return segments

    def flush(self) -> bytes:
        tail, self._tail = self._tail, b""
        return tail


def split_segments(segments: list[Segment]) -> tuple[bytes, list[MarkerEvent]]:
    """Convenience for callers that only want cleaned bytes and the events."""
    clean = b"".join(s for s in segments if isinstance(s, bytes))
    events = [s for s in segments if isinstance(s, MarkerEvent)]
    return clean, events


def _partial_marker_length(data: bytes) -> int:
    """Length of the longest suffix of ``data`` that could still grow into a marker."""
    window_start = max(0, len(data) - _MAX_MARKER_LEN)
    pos = data.find(ESC, window_start)
    while pos != -1:
        if _could_be_marker_prefix(data[pos:]):
            return len(data) - pos
        pos = data.find(ESC, pos + 1)
    return 0


def _could_be_marker_prefix(s: bytes) -> bool:
    """True if ``s`` is an incomplete marker: ESC ] 7331 ; (S | E ; -?digits) (BEL | ESC \\)."""
    n = min(len(s), len(OSC_PREFIX))
    if s[:n] != OSC_PREFIX[:n]:
        return False
    if len(s) <= len(OSC_PREFIX):
        return True
    rest = s[len(OSC_PREFIX) :]
    if rest[:1] == b"S":
        body_end = 1
    elif rest[:1] == b"E":
        if len(rest) == 1:
            return True
        if rest[1:2] != b";":
            return False
        i = 2
        if rest[i : i + 1] == b"-":
            i += 1
        while i < len(rest) and rest[i : i + 1].isdigit():
            i += 1
        body_end = i
    else:
        return False
    return rest[body_end:] in (b"", ESC)


def wrap_for_bash(command: str) -> str:
    """A multi-line command becomes one readline entry so the markers bracket it once.

    ``eval`` keeps the effect (cd, exports) in the shared shell, as P10 wants.
    """
    if "\n" not in command.strip():
        return command.strip()
    body = command.strip("\n")
    return f"eval \"$(cat <<'__HARNESS_EOF__'\n{body}\n__HARNESS_EOF__\n)\""


def wrap_for_powershell(command: str) -> str:
    """A multi-line command becomes one line: PSReadLine mangles typed here-strings.

    The body travels as base64 so no quoting or newline reaches the line editor;
    ``Invoke-Expression`` runs it in the shared scope, as P10 wants.
    """
    if "\n" not in command.strip():
        return command.strip()
    body = base64.b64encode(command.strip("\n").encode("utf-8")).decode("ascii")
    return (
        "Invoke-Expression ([Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{body}')))"
    )


_ESCAPE_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST (window titles, hyperlinks)
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI (colours, cursor movement, modes)
    r"|\x1b[PX^_][^\x1b]*\x1b\\"  # DCS / SOS / PM / APC strings
    r"|\x1b[()][0-9A-Za-z]"  # character set selection
    r"|\x1b[0-?@-Z\\-_]"  # two-character escapes (keypad modes, save cursor, ...)
)


def strip_escapes(text: str) -> str:
    """Terminal control sequences removed, for output handed to the model."""
    return _ESCAPE_RE.sub("", text)
