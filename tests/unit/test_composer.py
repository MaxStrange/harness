from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from harness.ui.chat.composer import Composer, common_prefix, complete_path  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "README.md").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "docs" / "notes.txt").write_text("x")
    return tmp_path


def test_complete_relative_and_absolute(tree):
    assert complete_path("./d", str(tree)) == ["./data/", "./docs/"]
    assert complete_path("./docs/", str(tree)) == ["./docs/notes.txt"]
    assert complete_path(str(tree) + "/RE", "/") == [str(tree) + "/README.md"]
    assert complete_path("../docs/no", str(tree / "data")) == ["../docs/notes.txt"]
    assert complete_path("./.h", str(tree)) == ["./.hidden"]  # dot files only when asked
    assert complete_path("plain word", str(tree)) == []
    assert complete_path("./nothing-here", str(tree)) == []


def test_complete_tilde(tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree))
    monkeypatch.setenv("USERPROFILE", str(tree))  # what expanduser reads on Windows
    assert complete_path("~/do", "/") == ["~/docs/"]
    assert "~/docs/" in complete_path("~", "/")


def test_common_prefix():
    assert common_prefix(["./docs/", "./data/"]) == "./d"
    assert common_prefix([]) == ""


def test_composer_tab_completes_unique_and_extends_prefix(qtbot, tree):
    composer = Composer(lambda: str(tree))
    qtbot.addWidget(composer)
    composer.input.setPlainText("read ./docs/n")
    composer.input.moveCursor(composer.input.textCursor().MoveOperation.End)
    composer.complete()
    assert composer.input.toPlainText() == "read ./docs/notes.txt"
    composer.input.setPlainText("look at ./")
    composer.input.moveCursor(composer.input.textCursor().MoveOperation.End)
    composer.complete()
    assert composer.choices.count() == 3  # data/, docs/, README.md
    composer.choices.picked.emit("./docs/")
    assert composer.input.toPlainText() == "look at ./docs/"
    composer.insert_text("/tmp/x.txt")
    assert composer.input.toPlainText() == "look at ./docs/ /tmp/x.txt"


def key(composer, which):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    k = {"up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down}[which]
    composer.input.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))


def test_up_in_an_empty_box_walks_the_history(qtbot, tree):
    composer = Composer(lambda: str(tree))
    qtbot.addWidget(composer)
    sent = []
    composer.send_requested.connect(sent.append)
    composer.set_history(["first", "second"])
    for text in ("third", "fourth"):
        composer.input.setPlainText(text)
        composer._submit()
    assert sent == ["third", "fourth"] and composer.input.toPlainText() == ""
    text = composer.input.toPlainText
    key(composer, "up")
    assert text() == "fourth"
    key(composer, "up")
    key(composer, "up")
    assert text() == "second"
    key(composer, "down")
    assert text() == "third"
    key(composer, "down")
    key(composer, "down")  # past the newest: an empty box again
    assert text() == ""
    key(composer, "down")  # Down on an empty box does nothing
    assert text() == ""


def test_up_does_not_replace_a_draft(qtbot, tree):
    composer = Composer(lambda: str(tree))
    qtbot.addWidget(composer)
    composer.set_history(["old message"])
    draft = "line one\nline two"
    composer.input.setPlainText(draft)
    key(composer, "up")  # moves the cursor inside the draft instead
    assert composer.input.toPlainText() == draft
    composer.input.clear()
    key(composer, "up")
    assert composer.input.toPlainText() == "old message"
    composer.input.insertPlainText(" edited")  # editing a recalled message stops browsing
    key(composer, "up")
    assert composer.input.toPlainText() == "old message edited"
