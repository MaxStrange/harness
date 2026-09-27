"""The message input: Enter sends, Shift+Enter inserts a newline, Stop cancels."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QWidget


class _Input(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    send_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = _Input()
        self.input.setPlaceholderText("Message the agent. Enter sends, Shift+Enter for a new line.")
        self.input.setMaximumHeight(140)
        self.input.submitted.connect(self._submit)
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
