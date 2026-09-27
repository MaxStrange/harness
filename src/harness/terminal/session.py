"""A terminal session: one shell in a pty, shared by the model and the user (P10).

Output flows to every subscriber (the xterm.js view) and through the marker
parser, which lets :meth:`TerminalSession.run_command` return exactly the
output and exit code of a command the model typed.
"""

from __future__ import annotations

import collections
import importlib.resources
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from harness.model.types import CancelToken
from harness.terminal.markers import MarkerParser, wrap_for_bash, wrap_for_powershell
from harness.terminal.pty_base import PtyProcess

log = logging.getLogger(__name__)

Subscriber = Callable[[bytes], None]
SCROLLBACK_BYTES = 2_000_000


@dataclass
class CommandResult:
    output: str
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class TerminalBusy(Exception):
    """The shell is not at a prompt (a program is running) or another command is in flight."""


def shell_kind_for(shell_path: str) -> str:
    name = Path(shell_path).name.lower()
    if name.startswith(("powershell", "pwsh")):
        return "powershell"
    return "bash"


def shell_argv(shell_path: str) -> list[str]:
    """The command line that starts the shell with the harness hooks installed."""
    scripts = importlib.resources.files("harness.terminal").joinpath("shell")
    if shell_kind_for(shell_path) == "powershell":
        init = str(scripts.joinpath("harness.ps1"))
        return [shell_path, "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", init]
    init = str(scripts.joinpath("harness.bashrc"))
    return [shell_path, "--rcfile", init, "-i"]


def spawn_pty(argv: list[str], cwd: str, env: dict[str, str], cols: int, rows: int) -> PtyProcess:
    if sys.platform == "win32":
        from harness.terminal.pty_windows import WindowsPty

        return WindowsPty(argv, cwd, env, cols, rows)
    from harness.terminal.pty_unix import UnixPty

    return UnixPty(argv, cwd, env, cols, rows)


class TerminalSession:
    def __init__(
        self,
        name: str,
        shell_path: str,
        cwd: str,
        *,
        cols: int = 120,
        rows: int = 30,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.shell_path = shell_path
        self.shell_kind = shell_kind_for(shell_path)
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self._env = dict(env or os.environ)
        self._env.setdefault("TERM", "xterm-256color")
        self._env["HARNESS_SESSION"] = name
        self._pty: PtyProcess | None = None
        self._reader: threading.Thread | None = None
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._parser = MarkerParser()
        self._scrollback: collections.deque[bytes] = collections.deque()
        self._scrollback_size = 0
        # Command capture state, protected by _cond.
        self._at_prompt = False
        self._capturing = False
        self._pending = False
        self._capture = bytearray()
        self._last_exit: int | None = None
        self._command_lock = threading.Lock()
        self.closed = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        argv = shell_argv(self.shell_path)
        self._pty = spawn_pty(argv, self.cwd, self._env, self.cols, self.rows)
        self._reader = threading.Thread(
            target=self._read_loop, name=f"pty-{self.name}", daemon=True
        )
        self._reader.start()
        log.info("terminal %s started: %s (pid %s) in %s", self.name, argv, self._pty.pid, self.cwd)

    def close(self) -> None:
        self.closed = True
        if self._pty is not None:
            self._pty.terminate()
        with self._cond:
            self._cond.notify_all()

    @property
    def alive(self) -> bool:
        return self._pty is not None and self._pty.is_alive() and not self.closed

    # -- I/O -----------------------------------------------------------------

    def subscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def scrollback(self) -> bytes:
        with self._lock:
            return b"".join(self._scrollback)

    def write(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if self._pty is None:
            raise TerminalBusy("terminal not started")
        self._pty.write(data)

    def interrupt(self) -> None:
        self.write(b"\x03")

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self._pty is not None:
            self._pty.resize(cols, rows)

    def _read_loop(self) -> None:
        assert self._pty is not None
        while not self.closed:
            try:
                data = self._pty.read()
            except EOFError:
                break
            if not data:
                time.sleep(0.01)
                continue
            segments = self._parser.feed(data)
            clean = bytearray()
            with self._cond:
                for segment in segments:
                    if isinstance(segment, bytes):
                        clean += segment
                        if self._capturing:
                            self._capture += segment
                    else:
                        self._handle_marker(segment.kind, segment.exit_code)
                self._cond.notify_all()
            if clean:
                self._broadcast(bytes(clean))
        self.closed = True
        with self._cond:
            self._at_prompt = False
            self._cond.notify_all()
        self._broadcast(b"\r\n[process exited]\r\n")

    def _handle_marker(self, kind: str, exit_code: int | None) -> None:
        if kind == "S":
            self._at_prompt = False
            if self._pending:
                self._pending = False
                self._capturing = True
                self._capture = bytearray()
        elif kind == "E":
            self._at_prompt = True
            if self._capturing:
                self._capturing = False
                self._last_exit = exit_code

    def _broadcast(self, data: bytes) -> None:
        with self._lock:
            self._scrollback.append(data)
            self._scrollback_size += len(data)
            while self._scrollback_size > SCROLLBACK_BYTES and len(self._scrollback) > 1:
                self._scrollback_size -= len(self._scrollback.popleft())
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(data)
            except Exception:  # noqa: BLE001
                log.exception("terminal subscriber failed")

    # -- commands ------------------------------------------------------------

    def wait_for_prompt(self, timeout: float = 10.0) -> bool:
        with self._cond:
            return (
                self._cond.wait_for(lambda: self._at_prompt or self.closed, timeout)
                and self._at_prompt
            )

    def run_command(
        self, command: str, *, timeout: float = 300.0, cancel: CancelToken | None = None
    ) -> CommandResult:
        """Type ``command`` into the shell and return its output and exit code (P11a)."""
        if not self.alive:
            raise TerminalBusy("the shell has exited; open a new terminal session")
        if not self._command_lock.acquire(blocking=False):
            raise TerminalBusy("another command is still running in this terminal")
        try:
            if not self.wait_for_prompt(5.0):
                raise TerminalBusy("the terminal is not at a prompt (a program is running in it)")
            wrapped = (
                wrap_for_powershell(command)
                if self.shell_kind == "powershell"
                else wrap_for_bash(command)
            )
            with self._cond:
                self._pending = True
                self._capturing = False
                self._capture = bytearray()
                self._last_exit = None
            self.write(wrapped + ("\r" if self.shell_kind == "powershell" else "\n"))
            deadline = time.monotonic() + timeout
            timed_out = cancelled = False
            with self._cond:
                while True:
                    finished = (not self._pending and not self._capturing) or self.closed
                    if finished:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    if cancel is not None and cancel.cancelled:
                        cancelled = True
                        break
                    self._cond.wait(min(0.1, remaining))
                output = bytes(self._capture)
                exit_code = self._last_exit
            if timed_out or cancelled:
                self.interrupt()
                with self._cond:
                    self._pending = False
                    self._capturing = False
            text = (
                output.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
            )
            return CommandResult(
                text.strip("\n"), exit_code, timed_out=timed_out, cancelled=cancelled
            )
        finally:
            self._command_lock.release()
