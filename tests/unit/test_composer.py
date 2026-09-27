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
