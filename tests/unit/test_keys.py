from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMainWindow,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from harness.ui import keys  # noqa: E402
from harness.ui.keys import OWNS_ALL_KEYS, KeyGuard, owns_all_keys  # noqa: E402


class Recorder(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.keys: list[int] = []

    def keyPressEvent(self, event):
        self.keys.append(event.key())
        super().keyPressEvent(event)


@pytest.fixture
def window(qtbot):
    win = QMainWindow()
    fired: list[str] = []
    for name, key in (("stop", "Escape"), ("new", "Ctrl+N"), ("terminal", "Ctrl+`")):
        action = QAction(name, win)
        action.setShortcut(key)
        action.triggered.connect(lambda _=False, n=name: fired.append(n))
        win.addAction(action)
    central = QWidget()
    layout = QVBoxLayout(central)
    terminal = QWidget()  # stands in for TerminalView; its inner widget is what has focus
    terminal.setProperty(OWNS_ALL_KEYS, True)
    inner = Recorder()
    QVBoxLayout(terminal).addWidget(inner)
    chat = Recorder()
    layout.addWidget(terminal)
    layout.addWidget(chat)
    win.setCentralWidget(central)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win, fired, inner, chat


@pytest.fixture
def guard():
    guard = KeyGuard()
    QApplication.instance().installEventFilter(guard)
    yield guard
    QApplication.instance().removeEventFilter(guard)


def press(qtbot, widget, key, modifier=Qt.KeyboardModifier.NoModifier):
    widget.setFocus()
    QApplication.processEvents()
    qtbot.keyClick(widget, key, modifier)


def test_without_the_guard_the_window_steals_escape(qtbot, window):
    """The bug: Escape in the terminal stopped the model instead of reaching Vim."""
    win, fired, inner, chat = window
    press(qtbot, inner, Qt.Key.Key_Escape)
    assert fired == ["stop"] and Qt.Key.Key_Escape not in inner.keys


def test_the_terminal_gets_escape_and_ctrl_keys(qtbot, window, guard):
    win, fired, inner, chat = window
    press(qtbot, inner, Qt.Key.Key_Escape)
    press(qtbot, inner, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    typed = [k for k in inner.keys if k != Qt.Key.Key_Control]  # Ctrl arrives on its own too
    assert fired == [] and typed == [Qt.Key.Key_Escape, Qt.Key.Key_N]
    press(qtbot, inner, Qt.Key.Key_QuoteLeft, Qt.KeyboardModifier.ControlModifier)
    assert fired == ["terminal"]  # Ctrl+` still toggles the terminal: the way out


def test_elsewhere_the_shortcuts_still_work(qtbot, window, guard):
    win, fired, inner, chat = window
    press(qtbot, chat, Qt.Key.Key_Escape)
    assert fired == ["stop"]  # Escape still stops the model from the chat


def test_owns_all_keys_looks_up_the_parents(qtbot):
    outer = QWidget()
    outer.setProperty(OWNS_ALL_KEYS, True)
    child = QWidget(outer)
    qtbot.addWidget(outer)
    assert owns_all_keys(child) and not owns_all_keys(QWidget()) and not owns_all_keys(None)


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(keys, "_guard", None)
    keys.install_key_guard()
    first = keys._guard
    keys.install_key_guard()
    assert keys._guard is first
    QApplication.instance().removeEventFilter(first)


def test_escape_reaches_the_shell_through_the_real_terminal_view(qtbot, guard):
    """End to end: xterm.js in QtWebEngine sends ESC to the shell; the window's Esc stays quiet."""
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from PySide6.QtGui import QAction as Action

    from harness.config import Config
    from harness.ui.viewers.terminal_view import TerminalView

    class Shell:
        name = "stub"

        def __init__(self):
            self.written = []

        def scrollback(self):
            return b"$ "

        def subscribe(self, callback):
            pass

        def unsubscribe(self, callback):
            pass

        def write(self, data):
            self.written.append(data if isinstance(data, bytes) else data.encode())

        def resize(self, cols, rows):
            pass

    win = QMainWindow()
    fired = []
    stop = Action("stop", win)
    stop.setShortcut("Escape")
    stop.triggered.connect(lambda: fired.append("stop"))
    win.addAction(stop)
    shell = Shell()
    view = TerminalView(shell, Config().terminal, Config().ui.theme)
    win.setCentralWidget(view)
    qtbot.addWidget(win)
    win.resize(600, 400)
    win.show()
    try:
        qtbot.waitUntil(lambda: view.bridge._ready, timeout=20000)
    except Exception:
        pytest.skip("QtWebEngine did not load the terminal page here")
    view.focus_terminal()
    qtbot.waitUntil(lambda: owns_all_keys(QApplication.focusWidget()), timeout=5000)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: b"\x1b" in shell.written, timeout=5000)
    assert fired == []
