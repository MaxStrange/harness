"""The message input: Enter sends, Shift+Enter inserts a newline, Tab completes a path, Stop cancels."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPlainTextEdit, QPushButton, QWidget

_PATH_START = re.compile(r"^(~|\.{1,2}[/\\]|/|[A-Za-z]:[/\\]|\\\\)")
MAX_CHOICES = 60


def complete_path(token: str, cwd: str) -> list[str]:
    """Completions for a path token, in the same form the user typed it (``~`` stays ``~``)."""
    if not _PATH_START.match(token):
        return []
    sep = "\\" if ("\\" in token and "/" not in token) else "/"
    if token in ("~", ".", ".."):
        directory_part, prefix = token, ""
    else:
        directory_part, _, prefix = token.rpartition(sep)
        if not directory_part:
            directory_part = sep if token.startswith(sep) else "."
    if re.fullmatch(r"[A-Za-z]:", directory_part):
        directory_part += sep  # a bare drive letter means its root, not the drive's cwd
    real_dir = Path(os.path.expanduser(directory_part))
    if not real_dir.is_absolute():
        real_dir = Path(cwd) / real_dir
    try:
        names = sorted(os.listdir(real_dir), key=str.lower)
    except OSError:
        return []
    fold = os.name == "nt"
    key = prefix.lower() if fold else prefix
    matches = []
    for name in names:
        if name.startswith(".") and not prefix.startswith("."):
            continue
        if (name.lower() if fold else name).startswith(key):
            shown = directory_part.rstrip(sep) + sep + name if directory_part != sep else sep + name
            if (real_dir / name).is_dir():
                shown += sep
            matches.append(shown)
    return matches[:MAX_CHOICES]


def common_prefix(items: list[str]) -> str:
    if not items:
        return ""
    first, last = min(items), max(items)
    i = 0
    while i < len(first) and i < len(last) and first[i] == last[i]:
        i += 1
    return first[:i]


class _Input(QPlainTextEdit):
    submitted = Signal()
    complete_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self.complete_requested.emit()
            return
        super().keyPressEvent(event)


class _Choices(QListWidget):
    """The popup with several completions; Enter, Tab or a click picks one."""

    picked = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.itemActivated.connect(lambda item: self.picked.emit(item.text()))
        self.itemClicked.connect(lambda item: self.picked.emit(item.text()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self.currentItem()
        ):
            self.picked.emit(self.currentItem().text())
            return
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    send_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, cwd_provider: Callable[[], str] | None = None) -> None:
        super().__init__()
        self.cwd_provider = cwd_provider or os.getcwd
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = _Input()
        self.input.setPlaceholderText(
            "Message the agent. Enter sends, Shift+Enter for a new line, Tab completes a path."
        )
        self.input.setMaximumHeight(140)
        self.input.submitted.connect(self._submit)
        self.input.complete_requested.connect(self.complete)
        layout.addWidget(self.input, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("accent")
        self.send_button.clicked.connect(self._submit)
        layout.addWidget(self.send_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setVisible(False)
        layout.addWidget(self.stop_button)
        self.choices = _Choices(self)
        self.choices.picked.connect(self._pick)
        self.choices.hide()
        self._token_span: tuple[int, int] | None = None

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def set_busy(self, busy: bool) -> None:
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def insert_text(self, text: str) -> None:
        """Insert at the cursor with a space before it when needed."""
        cursor = self.input.textCursor()
        before = self.input.toPlainText()[: cursor.position()]
        if before and not before[-1].isspace():
            text = " " + text
        cursor.insertText(text)
        self.input.setFocus()

    # -- completion ------------------------------------------------------------

    def _current_token(self) -> tuple[int, int, str]:
        cursor = self.input.textCursor()
        text = self.input.toPlainText()
        end = cursor.position()
        start = end
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        return start, end, text[start:end]

    def complete(self) -> None:
        start, end, token = self._current_token()
        matches = complete_path(token, self.cwd_provider())
        if not matches:
            return
        self._token_span = (start, end)
        if len(matches) == 1:
            self._replace_token(matches[0])
            return
        prefix = common_prefix(matches)
        if len(prefix) > len(token):
            self._replace_token(prefix)
            self._token_span = (start, start + len(prefix))
        self.choices.clear()
        self.choices.addItems(matches)
        self.choices.setCurrentRow(0)
        rect = self.input.cursorRect()
        width = max(240, min(520, self.input.width()))
        self.choices.resize(width, min(320, 22 * len(matches) + 8))
        self.choices.move(self.input.mapToGlobal(QPoint(rect.left(), rect.bottom() + 4)))
        self.choices.show()
        self.choices.setFocus()

    def _pick(self, text: str) -> None:
        self.choices.hide()
        self._replace_token(text)
        self.input.setFocus()

    def _replace_token(self, text: str) -> None:
        if self._token_span is None:
            return
        start, end = self._token_span
        cursor = self.input.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        self.input.setTextCursor(cursor)
        self._token_span = (start, start + len(text))
