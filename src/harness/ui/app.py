"""Application bootstrap: config, logging, core, agent, window."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from harness.agent.loop import Agent
from harness.bootstrap import build_core
from harness.config import ConfigError, load_config
from harness.logging_setup import setup_logging
from harness.paths import HarnessPaths

log = logging.getLogger(__name__)


def run(config_path: Path | None = None) -> int:
    # QtWebEngine needs these before QApplication exists.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-shader-disk-cache")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from harness.ui.bridge import AgentController, QtUiBridge
    from harness.ui.main_window import MainWindow
    from harness.ui.theme import build_stylesheet

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    if sys.platform == "win32":
        # Without its own application id Windows files the window under python.exe and shows
        # the Python icon in the taskbar instead of the window icon.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MaxStrange.AIHarness")
        except (AttributeError, OSError):  # pragma: no cover
            pass
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ai-harness")
    app.setDesktopFileName("ai-harness")
    paths = HarnessPaths()
    try:
        config = load_config(paths, config_path)
    except ConfigError as exc:
        QMessageBox.critical(None, "Configuration error", str(exc))
        print(exc, file=sys.stderr)
        return 2
    setup_logging(config.logging)
    log.info("harness starting; config %s", config_path or paths.config_file)
    app.setStyleSheet(build_stylesheet(config.ui))
    from harness.ui.main_window import make_app_icon

    app.setWindowIcon(make_app_icon(config.ui.theme))

    window_holder: dict = {}
    ui_bridge = QtUiBridge(
        lambda handoff: window_holder["window"].handoffs.open_external(handoff),
        lambda name, value: window_holder["window"].apply_preference(name, value),
    )
    core = build_core(config, paths, ui=ui_bridge)
    agent = Agent(
        config, core.main_model, core.registry, core.runner, core.services, core.store, paths=paths
    )
    controller = AgentController(agent, core.broker)
    window = MainWindow(core, agent, controller, ui_bridge)
    window_holder["window"] = window
    sessions = core.store.list_sessions(limit=1)
    if sessions:
        window.open_session(sessions[0].id)
    else:
        window.new_session()
    warmup = _warm_up_web_engine()
    if config.ui.panels.start_maximized:
        window.showMaximized()
    else:
        window.show()
    code = app.exec()
    del warmup
    log.info("harness exiting")
    core.shutdown()
    return code


def _warm_up_web_engine():
    """Create the first web view before the window is shown.

    QtWebEngine starts its GPU process and compositor on the first view, which
    makes the whole window flicker if that first view is the embedded terminal
    opened later; paying that cost here, offscreen, keeps it out of sight.
    """
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except ImportError:  # pragma: no cover
        return None
    view = QWebEngineView()
    view.setHtml("<html></html>")
    return view
