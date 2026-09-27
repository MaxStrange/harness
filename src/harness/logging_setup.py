"""Logging: three channels, each a rolling file under the logs directory (LOG1-LOG3).

Channels:

* ``internal`` - the harness's own code. Every module logs via
  ``logging.getLogger("harness.<module>")`` and ends up here.
* ``prompts``  - every prompt sent to a model and every response, verbatim.
* ``audit``    - every skill call, its arguments, approval decision and outcome.

Get a channel logger with :func:`prompts_log` / :func:`audit_log`; internal
loggers are ordinary ``logging.getLogger(__name__)`` calls.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from harness.config import LoggingConfig

INTERNAL = "harness"
PROMPTS = "harness_prompts"
AUDIT = "harness_audit"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def prompts_log() -> logging.Logger:
    return logging.getLogger(PROMPTS)


def audit_log() -> logging.Logger:
    return logging.getLogger(AUDIT)


def _rolling_handler(path: Path, cfg: LoggingConfig) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=int(cfg.rollover_mb * 1024 * 1024),
        backupCount=cfg.backups,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def setup_logging(cfg: LoggingConfig) -> Path:
    """Configure the three channels. Returns the log directory. Safe to call twice."""
    log_dir = Path(cfg.dir).expanduser()
    channels = {
        INTERNAL: ("internal.log", cfg.levels.internal),
        PROMPTS: ("prompts.log", cfg.levels.prompts),
        AUDIT: ("audit.log", cfg.levels.audit),
    }
    for name, (filename, level) in channels.items():
        logger = logging.getLogger(name)
        for old in list(logger.handlers):
            logger.removeHandler(old)
            old.close()
        logger.setLevel(level)
        logger.addHandler(_rolling_handler(log_dir / filename, cfg))
        # The prompt and audit channels are not part of the internal tree, so a
        # verbose prompt log never floods the internal one.
        logger.propagate = False
    if cfg.console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT))
        logging.getLogger(INTERNAL).addHandler(console)
    logging.getLogger(INTERNAL).debug("logging configured in %s", log_dir)
    return log_dir
