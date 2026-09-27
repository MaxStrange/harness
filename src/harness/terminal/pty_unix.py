"""Unix pseudo-terminal backend (Linux)."""

from __future__ import annotations

import fcntl
import os
import signal
import struct
import subprocess
import termios


class UnixPty:
    def __init__(
        self, argv: list[str], cwd: str, env: dict[str, str], cols: int = 120, rows: int = 30
    ) -> None:
        master, slave = os.openpty()
        self._master = master
        _set_winsize(slave, cols, rows)

        def child_setup() -> None:
            # Make the slave our controlling terminal (job control, Ctrl-C).
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self._proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            start_new_session=True,
            preexec_fn=child_setup,
            close_fds=True,
        )
        os.close(slave)

    @property
    def pid(self) -> int:
        return self._proc.pid

    def read(self, size: int = 65536) -> bytes:
        try:
            data = os.read(self._master, size)
        except OSError as exc:
            raise EOFError(str(exc)) from exc
        if not data:
            raise EOFError("pty closed")
        return data

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._master, view)
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        _set_winsize(self._master, cols, rows)
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGWINCH)
        except (ProcessLookupError, PermissionError):
            pass

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def terminate(self) -> None:
        if self.is_alive():
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        try:
            os.close(self._master)
        except OSError:
            pass


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
