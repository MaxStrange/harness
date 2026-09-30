"""The main window (UI2): dock | embedded views | chat | file explorer over sessions."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from harness.agent.loop import Agent
from harness.bootstrap import HarnessCore
from harness.config import ThemeConfig, set_config_value
from harness.skills.base import ApprovalDecision, Handoff
from harness.skills.runner import SkillOutcome
from harness.terminal.session import TerminalSession
from harness.ui.bridge import AgentController, QtUiBridge
from harness.ui.chat.chat_view import ChatView
from harness.ui.chat.composer import Composer
from harness.ui.critter import CRITTERS, StatusStrip
from harness.ui.dock import Dock
from harness.ui.file_explorer import FileExplorer
from harness.ui.handoff import HandoffExecutor
from harness.ui.keys import install_key_guard
from harness.ui.orbit_explorer import OrbitExplorer
from harness.ui.project_dialogs import ProjectDialog, TextFileDialog
from harness.ui.sessions_panel import SessionsPanel
from harness.ui.viewers.image_viewer import ImageViewer
from harness.ui.viewers.task_list_view import TaskListView
from harness.ui.viewers.terminal_view import TerminalPanel

log = logging.getLogger(__name__)

HEALTH_INTERVAL_MS = 60_000


class _Relay(QObject):
    """Worker-thread callbacks hop onto the GUI thread through these queued signals."""

    terminal_created = Signal(object)
    terminal_exited = Signal(object)
    tasks_changed = Signal(str, object)
    health = Signal(str, str, str)


def _is_within(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def make_app_icon(theme: ThemeConfig) -> QIcon:
    """The lizard on a rounded slate tile, drawn at several sizes for crisp taskbar icons."""
    from harness.ui.critter import Palette, draw_lizard

    icon = QIcon()
    palette = Palette(theme)
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(theme.surface_alt))
        painter.setPen(Qt.PenStyle.NoPen)
        radius = size * 0.22
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        # The critter drawing is 64x44; scale it to sit in the middle of the tile.
        scale = size / 64 * 0.92
        painter.translate(size * 0.04, (size - 44 * scale) / 2)
        painter.scale(scale, scale)
        draw_lizard(painter, palette, 0.0, False, False)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class MainWindow(QMainWindow):
    def __init__(
        self, core: HarnessCore, agent: Agent, controller: AgentController, ui_bridge: QtUiBridge
    ) -> None:
        super().__init__()
        install_key_guard()  # the terminal gets Escape etc. (Vim), not the window shortcuts
        self.core = core
        self.agent = agent
        self.controller = controller
        self.ui_bridge = ui_bridge
        self.config = core.config
        ui = self.config.ui
        self.theme = ui.theme
        self.handoffs = HandoffExecutor(self.config.handoff)
        self.relay = _Relay()
        self.setWindowTitle(ui.window_title)
        self.setWindowIcon(make_app_icon(self.theme))
        self.resize(ui.panels.window_width, ui.panels.window_height)

        # -- widgets -------------------------------------------------------
        self.dock = Dock(self.theme, ui.panels.dock_width)
        self.dock.add_item("terminal", "Terminal", ">_")
        self.dock.add_item("image", "Image viewer", "▣")
        self.dock.add_item("tasks", "Task list", "☑")
        self.dock.item_clicked.connect(self.toggle_view)

        self.side_panel = QStackedWidget()
        self.side_panel.setObjectName("panel")
        self.terminal_panel = TerminalPanel(self.config.terminal, self.theme)
        self.image_viewer = ImageViewer()
        self.task_view = TaskListView(core.task_lists)
        self._views = {
            "terminal": self.terminal_panel,
            "image": self.image_viewer,
            "tasks": self.task_view,
        }
        for view in self._views.values():
            self.side_panel.addWidget(view)
        self.side_panel.setVisible(False)

        self._approval_skills: dict[str, str] = {}  # approval id -> skill, for the status
        self.chat = ChatView(ui, self.perform_handoff, self._resolve_approval)
        self.composer = Composer(self._session_cwd)
        self.composer.send_requested.connect(self.send_message)
        self.composer.stop_requested.connect(self.controller.stop)
        chat_column = QWidget()
        chat_layout = QVBoxLayout(chat_column)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.addWidget(self.chat, 1)
        self.status_strip = StatusStrip(self.theme, ui.critter)
        chat_layout.addWidget(self.status_strip)
        chat_layout.addWidget(self.composer)

        explorer_root = str(Path(ui.file_explorer_root).expanduser())
        if ui.file_explorer == "orbit":
            self.explorer = OrbitExplorer(
                explorer_root,
                self.theme,
                ui.file_explorer_show_hidden,
                mouse_roll=ui.file_explorer_mouse_roll,
            )
            self.explorer.mouse_roll_changed.connect(
                lambda on: self.apply_preference("file_explorer_mouse_roll", on)
            )
            self.explorer.file_selected.connect(self.composer.insert_text)
            self.explorer.reveal_requested.connect(
                lambda path: self.perform_handoff(Handoff.file_manager(path))
            )
        else:
            self.explorer = FileExplorer(explorer_root)
        self.explorer.open_requested.connect(
            lambda path: self.perform_handoff(Handoff.editor(path))
        )
        self.explorer.cwd_requested.connect(self.set_cwd)
        self.sessions = SessionsPanel(core.store)
        self.sessions.new_requested.connect(self.new_session)
        self.sessions.open_requested.connect(self.open_session)
        self.sessions.delete_requested.connect(self.delete_session)
        self.sessions.rename_requested.connect(self.rename_session_to)
        self.sessions.new_project_requested.connect(self.new_project)
        self.sessions.edit_project_requested.connect(self.edit_project)
        self.sessions.delete_project_requested.connect(self.delete_project)
        self.sessions.move_requested.connect(self.move_session)
        self.sessions.global_context_requested.connect(self.edit_global_context)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.explorer)
        right.addWidget(self.sessions)
        right.setSizes(
            [
                ui.panels.explorer_height,
                max(200, ui.panels.window_height - ui.panels.explorer_height),
            ]
        )

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.side_panel)
        self.splitter.addWidget(chat_column)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes(
            [
                ui.panels.side_panel_width,
                ui.panels.window_width - ui.panels.side_panel_width - ui.panels.right_column_width,
                ui.panels.right_column_width,
            ]
        )

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.dock)
        root.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        # -- status bar ----------------------------------------------------
        self.model_status = QLabel("Main model: checking...")
        self.model_status.setObjectName("status")
        self.summarizer_status = QLabel("Summarizer: checking...")
        self.summarizer_status.setObjectName("status")
        self.reader_status = QLabel("Web reader: checking...")
        self.reader_status.setObjectName("status")
        self.cwd_label = QLabel("")
        self.cwd_label.setObjectName("status")
        self.turn_status = QLabel("")
        self.turn_status.setObjectName("status")
        bar = self.statusBar()
        bar.addWidget(self.model_status)
        bar.addWidget(self.summarizer_status)
        bar.addWidget(self.reader_status)
        bar.addWidget(self.turn_status, 1)
        bar.addPermanentWidget(self.cwd_label)

        self._build_menus()
        self._build_tray()
        self._connect_signals()
        self._start_health_checks()

    # -- construction helpers --------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._action("New session", self.new_session, "Ctrl+N"))
        file_menu.addAction(
            self._action(
                "Open config file",
                lambda: self.perform_handoff(Handoff.editor(self.core.paths.config_file)),
            )
        )
        file_menu.addAction(
            self._action(
                "Open log folder",
                lambda: self.perform_handoff(Handoff.file_manager(self.core.config.logging.dir)),
            )
        )
        file_menu.addSeparator()
        file_menu.addAction(self._action("Delete all sessions...", self.reset_sessions))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Quit", self.close, "Ctrl+Q"))
        session_menu = self.menuBar().addMenu("&Session")
        session_menu.addAction(self._action("Change working directory...", self.choose_cwd))
        session_menu.addAction(self._action("Rename...", self.rename_session))
        session_menu.addSeparator()
        session_menu.addAction(self._action("Global context...", self.edit_global_context))
        session_menu.addAction(self._action("Project context...", self.edit_current_project))
        session_menu.addAction(self._action("New project...", lambda: self.new_project(None)))
        session_menu.addSeparator()
        session_menu.addAction(self._action("Compact older turns now", self.controller.compact))
        session_menu.addAction(self._action("Stop generation", self.controller.stop, "Escape"))
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self._action("Terminal", lambda: self.show_view("terminal"), "Ctrl+`"))
        view_menu.addAction(self._action("Image viewer", lambda: self.show_view("image")))
        view_menu.addAction(self._action("Task list", lambda: self.show_view("tasks")))
        view_menu.addAction(self._action("Hide side panel", self.hide_side_panel))
        view_menu.addSeparator()
        critter_menu = view_menu.addMenu("Critter")
        self._critter_actions: dict[str, QAction] = {}
        for kind in CRITTERS:
            action = QAction(kind.capitalize(), self)
            action.setCheckable(True)
            action.setChecked(kind == self.config.ui.critter)
            action.triggered.connect(
                lambda _checked=False, k=kind: self.apply_preference("critter", k)
            )
            critter_menu.addAction(action)
            self._critter_actions[kind] = action
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action("Skills...", self.show_skills))
        help_menu.addAction(self._action("About", self.show_about))

    def _action(self, text: str, handler, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(handler)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip(self.config.ui.window_title)
            self.tray.messageClicked.connect(self.bring_to_front)
            self.tray.activated.connect(lambda _reason: self.bring_to_front())
            self.tray.show()

    def _connect_signals(self) -> None:
        s = self.controller.signals
        s.turn_started.connect(self._on_turn_started)
        s.text_delta.connect(self.chat.on_text_delta)
        s.assistant_message.connect(self.chat.on_assistant_message)
        s.skill_started.connect(self._on_skill_started)
        s.approval_needed.connect(self._on_approval_needed)
        s.skill_finished.connect(self._on_skill_finished)
        s.handoff_requested.connect(self.perform_handoff)
        s.status.connect(self._on_status)
        s.compacted.connect(
            lambda _summary: self.chat.add_notice("Earlier turns were compacted into a summary.")
        )
        s.turn_finished.connect(self._on_turn_finished)

        self.ui_bridge.notification.connect(self.show_notification)
        self.ui_bridge.clipboard.connect(lambda text: QApplication.clipboard().setText(text))
        self.ui_bridge.embedded_requested.connect(self.perform_handoff)

        self.relay.terminal_created.connect(self._on_terminal_created)
        self.core.terminals.on_created.append(self.relay.terminal_created.emit)
        self.relay.terminal_exited.connect(self._on_terminal_exited)
        self.core.terminals.on_exited.append(self.relay.terminal_exited.emit)
        self.terminal_panel.new_requested.connect(self.new_terminal)
        self.terminal_panel.restart_requested.connect(self.restart_terminal)
        self.terminal_panel.close_requested.connect(self.close_terminal)
        self.relay.tasks_changed.connect(self.task_view.on_store_changed)
        self.core.task_lists.listeners.append(self.relay.tasks_changed.emit)
        self.relay.health.connect(self._on_health)

    # -- sessions -----------------------------------------------------------------

    def new_session(self) -> None:
        if self.controller.busy:
            self._on_status("Stop the current turn before switching sessions.", True)
            return
        project_id = self.agent.session.project_id if self.agent.session else None
        session = self.agent.new_session(project_id=project_id)
        self._session_opened(session.id)

    def open_session(self, session_id: str) -> None:
        if self.controller.busy:
            self._on_status("Stop the current turn before switching sessions.", True)
            return
        try:
            self.agent.open_session(session_id)
        except KeyError:
            self.sessions.refresh()
            return
        self._session_opened(session_id)

    def _session_opened(self, session_id: str) -> None:
        assert self.agent.session is not None
        events = {
            e.message_seq: {"ok": e.ok, "handoff": e.handoff}
            for e in self.core.store.skill_events(session_id)
        }
        self.chat.load_transcript(self.agent.session.messages, events)
        self.composer.set_history(
            [
                m.content
                for m in self.agent.session.messages
                if m.role == "user" and m.content and not _is_synthetic_user_message(m.content)
            ]
        )
        project = self.core.store.get_project(self.agent.session.project_id)
        self.task_view.set_session(
            session_id, project.id if project else None, project.name if project else None
        )
        self.sessions.set_current(session_id, self.agent.session.project_id)
        title = self.config.ui.window_title
        self.setWindowTitle(f"{title} - {project.name}" if project else title)
        self.cwd_label.setText(str(self.agent.session.cwd))
        self.explorer.set_root(str(self.agent.session.cwd))
        self.composer.input.setFocus()

    def delete_session(self, session_id: str) -> None:
        if self.agent.session is not None and self.agent.session.id == session_id:
            if self.controller.busy:
                self._on_status("Stop the current turn before deleting this session.", True)
                return
        answer = QMessageBox.question(
            self, "Delete session", "Delete this session and its history?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.core.store.delete_session(session_id)
        if self.agent.session is not None and self.agent.session.id == session_id:
            self.new_session()
        else:
            self.sessions.refresh()

    def reset_sessions(self) -> None:
        """Factory reset of the chat history (the config, logs and skills stay)."""
        if self.controller.busy:
            self._on_status("Stop the current turn before deleting sessions.", True)
            return
        answer = QMessageBox.warning(
            self,
            "Delete all sessions",
            "Delete every saved session and its chat history? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.core.store.delete_all()
        self.new_session()
        self.chat.add_notice(f"Deleted {count} session(s).")

    # -- preferences -------------------------------------------------------------------

    def apply_preference(self, name: str, value: str | bool) -> str | None:
        """Change a preference now and persist it to the config file. Returns an error or None."""
        if name == "critter":
            if value not in CRITTERS:
                return f"unknown critter {value!r}"
            self.config.ui.critter = value
            self.status_strip.critter.set_kind(value)
            for kind, action in self._critter_actions.items():
                action.setChecked(kind == value)
        elif name == "file_explorer_mouse_roll":
            value = value if isinstance(value, bool) else str(value).lower() in ("true", "on", "1")
            self.config.ui.file_explorer_mouse_roll = value
            if isinstance(self.explorer, OrbitExplorer) and self.explorer.mouse_roll != value:
                self.explorer.set_mouse_roll(value)
        else:
            return f"unknown preference {name!r}"
        try:
            set_config_value(self.core.paths.config_file, f"ui.{name}", value)
        except (OSError, ValueError) as exc:
            log.warning("could not save %s to the config: %s", name, exc)
            return f"changed for this run, but saving to the config failed: {exc}"
        return None

    # -- projects and context ------------------------------------------------------

    def new_project(self, for_session: str | None = None) -> None:
        """Create a project. ``for_session`` (or, from the menu, the open session if it has no
        project) is moved into it."""
        dialog = ProjectDialog(self, "New project", root_dir=self._session_cwd())
        if dialog.exec() != ProjectDialog.DialogCode.Accepted:
            return
        name, root_dir, instructions = dialog.values()
        project = self.core.store.create_project(name, root_dir, instructions)
        target = for_session if isinstance(for_session, str) else None
        if (
            target is None
            and self.agent.session is not None
            and self.agent.session.project_id is None
        ):
            target = self.agent.session.id
        if target is not None:
            self.move_session(target, project.id)
        else:
            self.sessions.refresh()
        self.chat.add_notice(f"Project {name!r} created.")

    def edit_project(self, project_id: str) -> None:
        project = self.core.store.get_project(project_id)
        if project is None:
            return
        dialog = ProjectDialog(
            self,
            f"Project: {project.name}",
            name=project.name,
            root_dir=project.root_dir or "",
            instructions=project.instructions,
        )
        if dialog.exec() != ProjectDialog.DialogCode.Accepted:
            return
        name, root_dir, instructions = dialog.values()
        self.core.store.update_project(
            project_id, name=name, root_dir=root_dir, instructions=instructions
        )
        if root_dir and root_dir != (project.root_dir or "") and Path(root_dir).is_dir():
            # Sessions of this project follow the new root unless they already sit inside it.
            for record in self.core.store.list_sessions():
                if record.project_id == project_id and not _is_within(record.cwd, root_dir):
                    self.core.store.set_cwd(record.id, root_dir)
                    if self.agent.session is not None and self.agent.session.id == record.id:
                        self.agent.set_cwd(Path(root_dir))
        if self.agent.session is not None and self.agent.session.project_id == project_id:
            self.agent.set_project(project_id)  # rebuilds the system prompt
            self._session_opened(self.agent.session.id)
        self.sessions.refresh()

    def edit_current_project(self) -> None:
        if self.agent.session is None or self.agent.session.project_id is None:
            self.new_project()
        else:
            self.edit_project(self.agent.session.project_id)

    def delete_project(self, project_id: str) -> None:
        project = self.core.store.get_project(project_id)
        if project is None:
            return
        answer = QMessageBox.question(
            self, "Delete project", f"Delete project {project.name!r}? Its sessions are kept."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.core.store.delete_project(project_id)
        if self.agent.session is not None and self.agent.session.project_id == project_id:
            self.agent.set_project(None)
            self._session_opened(self.agent.session.id)
        self.sessions.refresh()

    def move_session(self, session_id: str, project_id: str | None) -> None:
        """Move a session into a project; it also adopts the project's root directory."""
        project = self.core.store.get_project(project_id)
        root = Path(project.root_dir) if project and project.root_dir else None
        if self.agent.session is not None and self.agent.session.id == session_id:
            self.agent.set_project(project_id)
            if root is not None and root.is_dir():
                self.agent.set_cwd(root)
            self._session_opened(session_id)
        else:
            self.core.store.set_session_project(session_id, project_id)
            if root is not None and root.is_dir():
                self.core.store.set_cwd(session_id, str(root))
        self.sessions.refresh()

    def edit_global_context(self) -> None:
        dialog = TextFileDialog(
            self,
            "Global context",
            self.core.paths.global_context_file,
            "Standing facts the model gets in every session: where your research papers are, "
            "which folder to use for scratch work, tools you prefer, and so on. Plain text or Markdown.",
        )
        if dialog.exec() == TextFileDialog.DialogCode.Accepted and self.agent.session is not None:
            self.agent._refresh_system_prompt()
            self.chat.add_notice("Global context updated.")

    def rename_session(self) -> None:
        if self.agent.session is None:
            return
        record = self.core.store.get_session(self.agent.session.id)
        title, ok = QInputDialog.getText(
            self, "Rename session", "Title:", text=record.title if record else ""
        )
        if ok and title.strip():
            self.rename_session_to(self.agent.session.id, title.strip())

    def rename_session_to(self, session_id: str, title: str) -> None:
        title = title.strip()
        if title:
            self.core.store.rename_session(session_id, title)
        self.sessions.refresh()

    def choose_cwd(self) -> None:
        if self.agent.session is None:
            return
        chosen = QFileDialog.getExistingDirectory(
            self, "Working directory", str(self.agent.session.cwd)
        )
        if chosen:
            self.set_cwd(chosen)

    def set_cwd(self, path: str) -> None:
        if self.agent.session is None:
            return
        self.agent.set_cwd(Path(path))
        self.cwd_label.setText(path)
        self.explorer.set_root(path)
        self.chat.add_notice(f"Working directory: {path}")

    # -- chat ---------------------------------------------------------------------

    def send_message(self, text: str) -> None:
        if self.agent.session is None:
            self.new_session()
        self.chat.add_user_message(text)
        if not self.controller.send(text):
            self._on_status("The agent is still working; press Stop first.", True)

    def _on_turn_started(self) -> None:
        self.composer.set_busy(True)
        self.turn_status.setText("Thinking...")
        self.status_strip.set_busy(True)
        self.status_strip.set_status("Thinking...")
        self.chat.on_turn_started()

    def _on_turn_finished(self, cancelled: bool) -> None:
        self.composer.set_busy(False)
        self.turn_status.setText("")
        self.status_strip.set_busy(False)
        self.chat.on_turn_finished(cancelled)
        self.sessions.refresh()

    def _on_status(self, text: str, error: bool) -> None:
        self.turn_status.setText(text)
        self.status_strip.set_status(text, error)
        self.turn_status.setObjectName("statusError" if error else "status")
        self.turn_status.style().unpolish(self.turn_status)
        self.turn_status.style().polish(self.turn_status)
        if error:
            self.chat.add_notice(text, error=True)
            log.warning("status: %s", text)

    def _on_approval_needed(self, pending) -> None:
        self._approval_skills[pending.id] = pending.request.skill
        self.chat.on_approval_needed(pending)
        self.turn_status.setText("Waiting for your approval")
        self.status_strip.set_status("Waiting for your approval")
        if not self.isActiveWindow():
            self.show_notification("Approval needed", pending.request.title)

    def _resolve_approval(self, approval_id: str, decision: ApprovalDecision) -> bool:
        resolved = self.core.broker.resolve(approval_id, decision)
        skill = self._approval_skills.pop(approval_id, None)
        if resolved:
            # The approval card was the last thing to set the status; say what happens now.
            if decision.approved:
                text = f"Running {skill}..." if skill else "Running..."
            else:
                text = "Denied; the model is told why"
            self.turn_status.setText(text)
            self.status_strip.set_status(text)
        return resolved

    def _on_skill_started(self, call_id: str, skill: str, args: dict) -> None:
        self.chat.on_skill_started(call_id, skill, args)
        self.status_strip.set_status(f"Running {skill}...")

    def _on_skill_finished(self, outcome: SkillOutcome) -> None:
        self.chat.on_skill_finished(outcome)
        self.turn_status.setText("Thinking...")
        self.status_strip.set_status("Thinking...")

    # -- handoffs and views -------------------------------------------------------

    def perform_handoff(self, handoff: Handoff) -> None:
        if handoff.kind == "external":
            error = self.handoffs.open_external(handoff)
            if error:
                self._on_status(f"Handoff failed: {error}", True)
        elif handoff.kind == "embedded":
            self.show_embedded(handoff)

    def show_embedded(self, handoff: Handoff) -> None:
        if handoff.action == "terminal":
            name = handoff.args.get("session", "main")
            cwd = handoff.target or (
                str(self.agent.session.cwd) if self.agent.session else str(Path.home())
            )
            session = self.core.terminals.get_or_create(name, cwd)
            self.terminal_panel.add_session(session)
            self.show_view("terminal")
            self.terminal_panel.show_session(name)
        elif handoff.action == "image":
            if handoff.target:
                self.image_viewer.show_image(handoff.target)
            self.show_view("image")
        elif handoff.action == "tasks":
            self.show_view("tasks")
        elif handoff.action == "explorer":
            if handoff.target:
                self.explorer.reveal(handoff.target)
        else:
            self._on_status(f"Unknown embedded view {handoff.action!r}", True)

    def show_view(self, name: str) -> None:
        view = self._views.get(name)
        if view is None:
            return
        if name == "terminal" and not self.terminal_panel.alive_names():
            self.restart_terminal(self.terminal_panel.current_name() or "main")
        self.side_panel.setCurrentWidget(view)
        self.side_panel.setVisible(True)
        self.dock.set_active(name)
        sizes = self.splitter.sizes()
        if sizes and sizes[0] < 100:
            sizes[0] = self.config.ui.panels.side_panel_width
            self.splitter.setSizes(sizes)

    def toggle_view(self, name: str) -> None:
        if not self.side_panel.isHidden() and self.dock.active == name:
            self.hide_side_panel()
        else:
            self.show_view(name)

    def hide_side_panel(self) -> None:
        self.side_panel.setVisible(False)
        self.dock.set_active(None)

    def _session_cwd(self) -> str:
        return str(self.agent.session.cwd) if self.agent.session else str(Path.home())

    def _on_terminal_created(self, session: TerminalSession) -> None:
        self.terminal_panel.add_session(session)

    def _on_terminal_exited(self, session: TerminalSession) -> None:
        self.terminal_panel.mark_exited(session.name)

    def new_terminal(self) -> None:
        name = self.core.terminals.next_name()
        session = self.core.terminals.get_or_create(name, self._session_cwd())
        self.terminal_panel.add_session(session)
        self.show_view("terminal")
        self.terminal_panel.show_session(name)

    def restart_terminal(self, name: str) -> None:
        """Start a fresh shell under ``name`` (the manager replaces a dead session)."""
        session = self.core.terminals.get_or_create(name, self._session_cwd())
        self.terminal_panel.add_session(session)
        self.terminal_panel.show_session(name)

    def close_terminal(self, name: str) -> None:
        self.core.terminals.close(name)
        self.terminal_panel.remove_session(name)

    # -- notifications and health -----------------------------------------------------

    def show_notification(self, title: str, body: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, body, self.windowIcon(), 8000)
        else:
            self.chat.add_notice(f"{title}: {body}")

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _start_health_checks(self) -> None:
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(HEALTH_INTERVAL_MS)
        self._health_timer.timeout.connect(self.check_health)
        self._health_timer.start()
        self.check_health()

    def check_health(self) -> None:
        def worker() -> None:
            main = self.core.main_model.health()
            summarizer_model = self.core.summarizer.model
            summarizer = (
                summarizer_model.health()
                if (self.core.summarizer.enabled and summarizer_model is not None)
                else "disabled"
            )
            reader_model = self.core.web_reader.model
            reader = (
                reader_model.health()
                if (self.core.web_reader.enabled and reader_model is not None)
                else "disabled"
            )
            self.relay.health.emit(main or "", summarizer or "", reader or "")

        threading.Thread(target=worker, name="health", daemon=True).start()

    def _on_health(self, main: str, summarizer: str, reader: str) -> None:
        self._set_health(self.model_status, "Main model", main)
        self._set_health(self.summarizer_status, "Summarizer", summarizer)
        self._set_health(self.reader_status, "Web reader", reader)

    def _set_health(self, label: QLabel, name: str, error: str) -> None:
        if not error:
            label.setText(f"{name}: online")
            label.setObjectName("statusOk")
        elif error == "disabled":
            label.setText(f"{name}: disabled")
            label.setObjectName("status")
        else:
            label.setText(f"{name}: offline")
            label.setObjectName("statusError")
        label.setToolTip(error if error and error != "disabled" else "")
        label.style().unpolish(label)
        label.style().polish(label)

    # -- dialogs ---------------------------------------------------------------------

    def show_skills(self) -> None:
        lines = [
            f"{s.name}{' (needs approval)' if s.needs_approval else ''}: {s.handoff_description}"
            for s in self.core.registry.all()
        ]
        if self.core.registry.problems:
            lines.append("\nProblems:")
            lines.extend(f"{p.source}: {p.error}" for p in self.core.registry.problems)
        QMessageBox.information(self, "Skills", "\n".join(lines))

    def show_about(self) -> None:
        from harness import __version__

        QMessageBox.about(
            self,
            "AI Harness",
            f"AI Harness v{__version__}\nConfig: {self.core.paths.config_file}\nLogs: {self.config.logging.dir}",
        )

    def closeEvent(self, event) -> None:
        self.controller.stop()
        self._health_timer.stop()
        for name in self.terminal_panel.names():
            self.terminal_panel.remove_session(name)
        super().closeEvent(event)


def _is_synthetic_user_message(content: str) -> bool:
    """User-role messages the harness wrote (compaction summaries, text-mode tool results)."""
    from harness.model.compaction import SUMMARY_PREFIX

    return content.startswith(SUMMARY_PREFIX) or content.startswith("[Result of ")
