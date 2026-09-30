"""Keys a focused widget must get even though the window has a shortcut for them.

The terminal runs programs like Vim, where Escape, Ctrl+N or Ctrl+Q mean
something; the window's shortcuts (Escape stops the model, Ctrl+N is a new
session) would otherwise take those keys first. A widget that sets the
``ownsAllKeys`` property gets every key while it, or anything inside it (the
web view's internal widgets), has focus. PASS_THROUGH keeps a way out.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

OWNS_ALL_KEYS = "ownsAllKeys"
# Still shortcuts inside such a widget: Ctrl+` toggles the terminal panel, so you can leave it.
PASS_THROUGH = {QKeySequence("Ctrl+`").toString()}


def owns_all_keys(widget: QWidget | None) -> bool:
    """Whether ``widget`` or one of its parents asked for every key."""
    while widget is not None:
        if widget.property(OWNS_ALL_KEYS):
            return True
        widget = widget.parentWidget()
    return False


class KeyGuard(QObject):
    """Application event filter: shortcuts give way to widgets that own all keys."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and owns_all_keys(
            QApplication.focusWidget()
        ):
            combined = event.keyCombination().toCombined()
            if QKeySequence(combined).toString() not in PASS_THROUGH:
                # Accepting the override tells Qt the widget wants this key: the shortcut does
                # not fire and the key press is delivered to the widget as usual.
                event.accept()
                return True
        return False


_guard: KeyGuard | None = None


def install_key_guard() -> None:
    """Install the guard once for the application."""
    global _guard
    app = QApplication.instance()
    if app is not None and _guard is None:
        _guard = KeyGuard(app)
        app.installEventFilter(_guard)
