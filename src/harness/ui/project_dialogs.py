"""Dialogs for projects and the global context file."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProjectDialog(QDialog):
    """Name, root directory and instructions for a project."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        *,
        name: str = "",
        root_dir: str = "",
        instructions: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("e.g. Thesis, Home automation")
        form.addRow("Name", self.name_edit)
        root_row = QHBoxLayout()
        self.root_edit = QLineEdit(root_dir)
        self.root_edit.setPlaceholderText("Optional: new sessions start in this directory")
        root_row.addWidget(self.root_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        root_row.addWidget(browse)
        form.addRow("Root directory", root_row)
        layout.addLayout(form)
        hint = QLabel(
            "Instructions go into the model's context for every session in this project: "
            "what the project is, where things live, how you want the work done."
        )
        hint.setWordWrap(True)
        hint.setObjectName("status")
        layout.addWidget(hint)
        self.instructions_edit = QPlainTextEdit(instructions)
        self.instructions_edit.setPlaceholderText(
            "e.g. This is my PhD thesis. Papers live in ~/research/papers, drafts in ~/thesis/draft. "
            "Use ~/thesis/scratch for temporary files. Cite with BibTeX keys from refs.bib."
        )
        layout.addWidget(self.instructions_edit, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Project root", self.root_edit.text() or str(Path.home())
        )
        if chosen:
            self.root_edit.setText(chosen)

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Project", "The project needs a name.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.root_edit.text().strip(),
            self.instructions_edit.toPlainText(),
        )


class TextFileDialog(QDialog):
    """Edit a text file in place (used for the global context, ~/.harness/context.md)."""

    def __init__(self, parent: QWidget | None, title: str, path: Path, hint: str = "") -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(title)
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        if hint:
            label = QLabel(hint)
            label.setWordWrap(True)
            label.setObjectName("status")
            layout.addWidget(label)
        self.editor = QPlainTextEdit()
        try:
            self.editor.setPlainText(path.read_text(encoding="utf-8") if path.exists() else "")
        except OSError as exc:
            self.editor.setPlainText("")
            QMessageBox.warning(self, title, f"Could not read {path}: {exc}")
        layout.addWidget(self.editor, 1)
        where = QLabel(str(path))
        where.setObjectName("status")
        layout.addWidget(where)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, self.windowTitle(), f"Could not write {self.path}: {exc}")
            return
        self.accept()
