"""Assemble the harness core (everything except the GUI) from a config.

Used by the Qt app, by the stack tests and by the CLI checks, so all of them
build the same objects the same way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from harness.agent.store import SessionStore
from harness.config import Config
from harness.model.roles import RoleClient
from harness.model.stack import ModelStack
from harness.model.summarizer import Summarizer
from harness.model.web_reader import WebReader
from harness.paths import HarnessPaths
from harness.security.net import NetPolicy
from harness.security.paths import PathPolicy
from harness.skills.base import RecordingUi, Services, UiBridge
from harness.skills.registry import SkillRegistry
from harness.skills.runner import ApprovalBroker, SkillRunner
from harness.skills.tasklist_store import TaskListStore
from harness.terminal.manager import BackgroundJobs, TerminalManager
from harness.web.fetch import SafeFetcher
from harness.web.searxng import SearxClient

log = logging.getLogger(__name__)


@dataclass
class HarnessCore:
    config: Config
    paths: HarnessPaths
    main_model: RoleClient
    summarizer: Summarizer
    web_reader: WebReader
    registry: SkillRegistry
    services: Services
    broker: ApprovalBroker
    runner: SkillRunner
    store: SessionStore
    terminals: TerminalManager
    jobs: BackgroundJobs
    task_lists: TaskListStore
    stack: ModelStack | None = None

    def shutdown(self) -> None:
        self.runner.shutdown()
        if self.stack is not None:
            self.stack.close()
        self.terminals.close_all()
        self.store.close()


def build_models(config: Config) -> tuple[RoleClient, Summarizer, WebReader]:
    m = config.models
    main = RoleClient.from_endpoints(
        "main",
        m.main.endpoints,
        tool_format=m.main.tool_format,
        temperature=m.main.temperature,
        max_tokens=m.main.max_tokens,
    )
    summarizer_model = (
        RoleClient.from_endpoints(
            "summarizer",
            m.summarizer.endpoints,
            temperature=m.summarizer.temperature,
            max_tokens=m.summarizer.max_tokens,
        )
        if m.summarizer.enabled and m.summarizer.endpoints
        else None
    )
    reader_model = (
        RoleClient.from_endpoints(
            "web_reader",
            m.web_reader.endpoints,
            temperature=m.web_reader.temperature,
            max_tokens=m.web_reader.max_tokens,
        )
        if m.web_reader.enabled and m.web_reader.endpoints
        else None
    )
    summarizer = Summarizer(summarizer_model, enabled=m.summarizer.enabled)
    web_reader = WebReader(
        reader_model, enabled=m.web_reader.enabled, max_input_chars=m.web_reader.max_input_chars
    )
    return main, summarizer, web_reader


def build_core(
    config: Config,
    paths: HarnessPaths | None = None,
    ui: UiBridge | None = None,
    broker: ApprovalBroker | None = None,
) -> HarnessCore:
    paths = paths or HarnessPaths()
    paths.ensure_directories()
    main, summarizer, web_reader = build_models(config)

    net_policy = NetPolicy.from_config(config.web.allowed_private_hosts, config.web.searxng_url)
    path_policy = PathPolicy.from_config(config.security, config.web)
    terminals = TerminalManager(config.terminal)
    task_lists = TaskListStore()
    jobs = BackgroundJobs(terminals)
    stack = ModelStack(config.models.stack) if config.models.stack.enabled else None
    services = Services(
        path_policy=path_policy,
        net_policy=net_policy,
        fetcher=SafeFetcher(net_policy, config.web),
        searx=SearxClient(
            config.web.searxng_url,
            timeout_s=config.web.fetch_timeout_s,
            user_agent=config.web.user_agent,
        ),
        web_reader=web_reader,
        terminals=terminals,
        ui=ui or RecordingUi(),
        task_lists=task_lists,
        background_jobs=jobs,
        stack=stack,
    )

    registry = SkillRegistry()
    registry.load_builtin()
    registry.load_directory(paths.user_skills_dir)
    for extra in config.skills.extra_dirs:
        registry.load_directory(Path(extra))
    registry.disable(config.skills.disabled)
    for problem in registry.problems:
        log.error("skill load problem in %s: %s", problem.source, problem.error)

    broker = broker or ApprovalBroker()
    runner = SkillRunner(
        registry,
        broker,
        summarizer,
        default_timeout_s=config.skills.default_timeout_s,
        timeouts=config.skills.timeouts,
        max_result_chars=config.skills.max_result_chars,
    )
    store = SessionStore(config.sessions.db_path)
    # Every change to a checklist (by the model or in the panel) is saved at once.
    task_lists.loader, task_lists.saver = store.load_task_list, store.save_task_list
    return HarnessCore(
        config,
        paths,
        main,
        summarizer,
        web_reader,
        registry,
        services,
        broker,
        runner,
        store,
        terminals,
        jobs,
        task_lists,
        stack,
    )
