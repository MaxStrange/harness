"""The ``harness`` command (P18)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness import __version__
from harness.config import ConfigError, default_config_text, load_config
from harness.paths import HarnessPaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="Personal AI harness (Qt GUI).")
    parser.add_argument(
        "--config", type=Path, help="config file to use instead of ~/.harness/config.yml"
    )
    parser.add_argument(
        "--check-config", action="store_true", help="validate the config file and exit"
    )
    parser.add_argument(
        "--print-default-config",
        action="store_true",
        help="print the commented default config and exit",
    )
    parser.add_argument(
        "--stack-check",
        action="store_true",
        help="check that the configured model servers and SearXNG are reachable, then exit",
    )
    parser.add_argument("--version", action="version", version=f"harness {__version__}")
    args = parser.parse_args(argv)

    if args.print_default_config:
        sys.stdout.write(default_config_text())
        return 0
    if args.check_config:
        try:
            load_config(HarnessPaths(), args.config)
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"config OK: {args.config or HarnessPaths().config_file}")
        return 0
    if args.stack_check:
        return stack_check(args.config)
    from harness.ui.app import run

    return run(args.config)


def stack_check(config_path: Path | None) -> int:
    """A quick, GUI-free reachability report (the full stack tests live in tests/stack)."""
    from harness.bootstrap import build_models
    from harness.web.searxng import SearchError, SearxClient

    try:
        config = load_config(HarnessPaths(), config_path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    main, summarizer, reader = build_models(config)
    failures = 0
    for name, role in (
        ("main model", main),
        ("summarizer", summarizer.model),
        ("web reader", reader.model),
    ):
        if role is None:
            print(f"{name}: disabled")
            continue
        error = role.health()
        print(
            f"{name}: {'online via ' + role.active.name if error is None and role.active else 'OFFLINE - ' + str(error)}"
        )
        failures += error is not None and name == "main model"
    try:
        results = SearxClient(config.web.searxng_url, timeout_s=10).search(
            "harness self test", count=1
        )
        print(f"searxng: online ({len(results)} result(s) for a test query)")
    except SearchError as exc:
        print(f"searxng: OFFLINE - {exc}")
    return 1 if failures else 0
