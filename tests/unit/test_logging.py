from __future__ import annotations

import logging

from harness.logging_setup import audit_log, prompts_log, setup_logging


def test_three_channels_write_separate_files(config):
    log_dir = setup_logging(config.logging)
    logging.getLogger("harness.test").info("internal hello")
    prompts_log().info("prompt hello")
    audit_log().info("audit hello")
    for handler in logging.getLogger("harness").handlers:
        handler.flush()
    assert "internal hello" in (log_dir / "internal.log").read_text()
    assert "prompt hello" in (log_dir / "prompts.log").read_text()
    assert "audit hello" in (log_dir / "audit.log").read_text()
    assert "prompt hello" not in (log_dir / "internal.log").read_text()


def test_rollover_size_from_config(config):
    config.logging.rollover_mb = 1
    setup_logging(config.logging)
    handler = logging.getLogger("harness").handlers[0]
    assert handler.maxBytes == 1024 * 1024
