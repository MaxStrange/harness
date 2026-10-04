"""Named terminal sessions and background jobs shared by the skills and the UI."""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from harness.config import TerminalConfig
from harness.model.types import CancelToken
from harness.terminal.session import CommandResult, TerminalSession

log = logging.getLogger(__name__)


class TerminalManager:
    """Creates sessions on demand. The UI is told about new sessions via ``on_created``."""

    def __init__(self, config: TerminalConfig) -> None:
        self.config = config
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self.on_created: list[Callable[[TerminalSession], None]] = []
        self.on_exited: list[Callable[[TerminalSession], None]] = []

    @property
    def shell_path(self) -> str:
        return self.config.windows_shell if sys.platform == "win32" else self.config.linux_shell

    def get(self, name: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return list(self._sessions)

    def free_name(self, base: str) -> str:
        """``base``, or ``base 2``, ``base 3`` ... whichever no live session uses."""
        with self._lock:
            taken = {n for n, s in self._sessions.items() if s.alive}
        name, n = base, 2
        while name in taken:
            name, n = f"{base} {n}", n + 1
        return name

    def get_or_create(self, name: str, cwd: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(name)
            if session is not None and session.alive:
                return session
            session = TerminalSession(name, self.shell_path, cwd, env=None)
            session.on_exit.append(self._session_exited)
            session.start()
            self._sessions[name] = session
        for callback in list(self.on_created):
            try:
                callback(session)
            except Exception:  # noqa: BLE001
                log.exception("on_created callback failed")
        return session

    def _session_exited(self, session: TerminalSession) -> None:
        for callback in list(self.on_exited):
            try:
                callback(session)
            except Exception:  # noqa: BLE001
                log.exception("on_exited callback failed")

    def next_name(self, prefix: str = "term") -> str:
        """A fresh session name: main, term-2, term-3, ..."""
        with self._lock:
            n = 2
            while f"{prefix}-{n}" in self._sessions:
                n += 1
            return f"{prefix}-{n}"

    def close(self, name: str) -> None:
        with self._lock:
            session = self._sessions.pop(name, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        for name in self.names():
            self.close(name)


@dataclass
class BackgroundJob:
    id: str
    command: str
    session_name: str
    cwd: str
    result: CommandResult | None = None
    error: str | None = None
    read_offset: int = 0
    cancel: CancelToken = field(default_factory=CancelToken)
    thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.result is None and self.error is None


class BackgroundJobs:
    """S17: each job runs in its own terminal session so the user can attach to it."""

    def __init__(self, terminals: TerminalManager) -> None:
        self.terminals = terminals
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = threading.Lock()

    def start(self, command: str, cwd: str, timeout_s: float = 24 * 3600) -> BackgroundJob:
        job_id = uuid.uuid4().hex[:8]
        session_name = f"job-{job_id}"
        job = BackgroundJob(job_id, command, session_name, cwd)
        session = self.terminals.get_or_create(session_name, cwd)

        def run() -> None:
            try:
                job.result = session.run_command(command, timeout=timeout_s, cancel=job.cancel)
            except Exception as exc:  # noqa: BLE001
                job.error = f"{exc.__class__.__name__}: {exc}"

        job.thread = threading.Thread(target=run, name=f"bg-{job_id}", daemon=True)
        with self._lock:
            self._jobs[job_id] = job
        job.thread.start()
        return job

    def get(self, job_id: str) -> BackgroundJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[BackgroundJob]:
        with self._lock:
            return list(self._jobs.values())

    def new_output(self, job: BackgroundJob) -> str:
        """Output produced since the last check."""
        session = self.terminals.get(job.session_name)
        if session is None:
            return ""
        data = session.scrollback()
        chunk = data[job.read_offset :]
        job.read_offset = len(data)
        return chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")

    def stop(self, job: BackgroundJob) -> None:
        job.cancel.cancel()
        session = self.terminals.get(job.session_name)
        if session is not None:
            session.interrupt()
