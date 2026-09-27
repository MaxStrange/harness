"""Performing external handoffs (H2): editor, file manager, browser, default application."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from harness.config import HandoffConfig
from harness.skills.base import Handoff

log = logging.getLogger(__name__)


class HandoffExecutor:
    def __init__(self, config: HandoffConfig) -> None:
        self.config = config

    def open_external(self, handoff: Handoff) -> str | None:
        """Returns an error message, or None on success."""
        try:
            if handoff.action == "editor":
                return self._open_editor(handoff.target or "", handoff.line)
            if handoff.action == "file_manager":
                return self._open_file_manager(handoff.target or "")
            if handoff.action in ("browser", "default_app"):
                return self._open_default(handoff.target or "")
            return f"unknown external handoff action {handoff.action!r}"
        except Exception as exc:  # noqa: BLE001
            log.exception("handoff failed")
            return f"{exc.__class__.__name__}: {exc}"

    def _run(self, argv: list[str]) -> str | None:
        if not argv:
            return "empty command"
        executable = shutil.which(argv[0])
        if executable is None:
            return f"{argv[0]!r} is not installed or not on PATH (change handoff settings in the config)"
        command = [executable, *argv[1:]]
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if executable.lower().endswith((".cmd", ".bat")):
                # VS Code's `code` is code.cmd: batch files need the command interpreter.
                command = ["cmd.exe", "/d", "/c", *command]
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except OSError as exc:
            return f"could not start {executable}: {exc}"
        return None

    def _open_editor(self, path: str, line: int | None) -> str | None:
        # Split the template before substituting so paths with spaces or backslashes survive.
        argv = [part.format(path=path, line=line or 1) for part in shlex.split(self.config.editor)]
        error = self._run(argv)
        if error is None:
            return None
        log.warning("editor handoff failed (%s); falling back to the default application", error)
        fallback = self._open_default(path)
        return None if fallback is None else f"{error}; fallback also failed: {fallback}"

    def _open_file_manager(self, path: str) -> str | None:
        target = Path(path)
        if self.config.file_manager:
            argv = [
                part.format(path=str(target), line=0)
                for part in shlex.split(self.config.file_manager)
            ]
            return self._run(argv)
        if sys.platform == "win32":
            if target.is_dir():
                return self._run(["explorer", str(target)])
            return self._run(["explorer", f"/select,{target}"])
        if sys.platform == "darwin":
            return self._run(
                ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
            )
        folder = target if target.is_dir() else target.parent
        return self._open_default(str(folder))

    @staticmethod
    def _open_default(target: str) -> str | None:
        url = QUrl(target) if "://" in target else QUrl.fromLocalFile(target)
        if not QDesktopServices.openUrl(url):
            return f"the system could not open {target}"
        return None
