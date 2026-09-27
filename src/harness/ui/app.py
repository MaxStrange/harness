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
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ai-harness")
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

    window_holder: dict = {}
    ui_bridge = QtUiBridge(lambda handoff: window_holder["window"].handoffs.open_external(handoff))
    core = build_core(config, paths, ui=ui_bridge)
    agent = Agent(config, core.main_model, core.registry, core.runner, core.services, core.store)
    controller = AgentController(agent, core.broker)
    window = MainWindow(core, agent, controller, ui_bridge)
    window_holder["window"] = window
    sessions = core.store.list_sessions(limit=1)
    if sessions:
        window.open_session(sessions[0].id)
    else:
        window.new_session()
    window.show()
    code = app.exec()
    log.info("harness exiting")
    core.shutdown()
    return code
