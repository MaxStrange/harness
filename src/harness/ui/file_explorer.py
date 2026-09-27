"""File explorer (UI3). Milestone 1 stub: a standard tree view. The orbiting view comes later."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QFileSystemModel, QLabel, QMenu, QTreeView, QVBoxLayout, QWidget


class FileExplorer(QWidget):
    open_requested = Signal(str)  # a file to open in the editor
    cwd_requested = Signal(str)  # "use this directory as the session working directory"

    def __init__(self, root: str) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Files")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        self.model = QFileSystemModel(self)
        self.model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden
        )
        self.model.setRootPath(root)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(root))
        for column in (1, 2, 3):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.doubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree)

    def set_root(self, root: str) -> None:
        self.model.setRootPath(root)
        self.tree.setRootIndex(self.model.index(root))

    def reveal(self, path: str) -> None:
        index = self.model.index(path)
        if index.isValid():
            self.tree.scrollTo(index)
            self.tree.setCurrentIndex(index)
            self.tree.expand(index)

    def _double_clicked(self, index: QModelIndex) -> None:
        path = self.model.filePath(index)
        if Path(path).is_file():
            self.open_requested.emit(path)

    def _context_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        path = Path(self.model.filePath(index))
        menu = QMenu(self)
        directory = path if path.is_dir() else path.parent
        menu.addAction("Use as working directory", lambda: self.cwd_requested.emit(str(directory)))
        if path.is_file():
            menu.addAction("Open in editor", lambda: self.open_requested.emit(str(path)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
