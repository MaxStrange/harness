"""Windows pseudo-terminal backend: pywinpty over ConPTY (P11, C3)."""

from __future__ import annotations


class WindowsPty:
    def __init__(
        self, argv: list[str], cwd: str, env: dict[str, str], cols: int = 120, rows: int = 30
    ) -> None:
        from winpty import PtyProcess  # imported lazily: only installed on Windows

        self._proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))

    @property
    def pid(self) -> int:
        return self._proc.pid

    def read(self, size: int = 65536) -> bytes:
        try:
            text = self._proc.read(size)
        except EOFError:
            raise
        except Exception as exc:  # noqa: BLE001 - winpty raises assorted errors on exit
            raise EOFError(str(exc)) from exc
        if not text:
            if not self._proc.isalive():
                raise EOFError("process exited")
            return b""
        return text.encode("utf-8", errors="replace")

    def write(self, data: bytes) -> None:
        self._proc.write(data.decode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int) -> None:
        self._proc.setwinsize(rows, cols)

    def is_alive(self) -> bool:
        return self._proc.isalive()

    def terminate(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass
