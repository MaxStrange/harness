"""The small pty interface both platforms implement."""

from __future__ import annotations

from typing import Protocol


class PtyProcess(Protocol):
    def read(self, size: int = 65536) -> bytes:
        """Block until output is available. Raise EOFError when the process is gone."""
        ...

    def write(self, data: bytes) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    @property
    def pid(self) -> int: ...
