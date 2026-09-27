"""Configuration schema, loading, validation and first-run defaults.

The schema is the single source of truth for what the config file may contain.
Every field has a default, so a missing setting is never an error; an invalid
one produces a :class:`ConfigError` naming the setting and the problem.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from harness.paths import HarnessPaths, default_home

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


class StrictModel(BaseModel):
    """Base for all config sections: unknown keys are an error (typos get caught)."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


def _expand(path: str | None) -> str | None:
    if path is None:
        return None
    return str(Path(path).expanduser())


def _in_home(*parts: str) -> str:
    """A default location under the harness home (``~/.harness`` unless HARNESS_HOME is set)."""
    return str(default_home().joinpath(*parts))


LLM_ROUTER_URL = "http://10.0.0.228:18080/v1"


def _default_api_key_file() -> str:
    return _in_home("secrets", "llama-api-key")


class Endpoint(StrictModel):
    base_url: str = Field(description="OpenAI-compatible base URL, ending in /v1")
    model: str = "default"
    api_key: str | None = None
    # A file holding the key (first line), so the config itself carries no secret.
    api_key_file: str | None = None
    timeout_s: float = Field(default=120, gt=0)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("api_key_file")
    @classmethod
    def _expand_key_file(cls, value: str | None) -> str | None:
        return _expand(value)

    @model_validator(mode="after")
    def _one_key_source(self) -> Endpoint:
        if self.api_key and self.api_key_file:
            raise ValueError("set api_key or api_key_file, not both")
        return self

    def resolve_api_key(self) -> str | None:
        """The key to send, read from ``api_key_file`` if set. Raises ConfigError if unreadable."""
        if self.api_key_file is None:
            return self.api_key
        try:
            lines = Path(self.api_key_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ConfigError(
                f"api_key_file {self.api_key_file} cannot be read: {exc.strerror or exc}"
            ) from exc
        key = lines[0].strip() if lines else ""
        if not key:
            raise ConfigError(f"api_key_file {self.api_key_file} is empty")
        return key


def _router_endpoint(model: str, timeout_s: float = 120) -> Endpoint:
    return Endpoint(
        base_url=LLM_ROUTER_URL,
        model=model,
        api_key_file=_default_api_key_file(),
        timeout_s=timeout_s,
    )


class MainModelConfig(StrictModel):
    endpoints: list[Endpoint] = Field(
        default_factory=lambda: [_router_endpoint("gpt-oss-120b")],
        min_length=1,
    )
    temperature: float = Field(default=0.2, ge=0)
    max_tokens: int = Field(default=4096, gt=0)
    # Below the server's 131072: the compaction estimate (~4 chars/token, no tool schemas)
    # undercounts code and JSON, so the real prompt can run well past it.
    context_window: int = Field(default=98304, gt=0)
    tool_format: Literal["native", "text"] = "native"
    compaction_threshold: float = Field(default=0.8, gt=0, le=1)
    compaction_keep_last: int = Field(default=6, ge=1)
    auto_compact: bool = True


class SummarizerConfig(StrictModel):
    enabled: bool = True
    endpoints: list[Endpoint] = Field(
        default_factory=lambda: [_router_endpoint("qwen3-coder-30b", timeout_s=20)]
    )
    temperature: float = Field(default=0.0, ge=0)
    max_tokens: int = Field(default=256, gt=0)


class WebReaderConfig(StrictModel):
    enabled: bool = True
    endpoints: list[Endpoint] = Field(default_factory=lambda: [_router_endpoint("qwen3-coder-30b")])
    temperature: float = Field(default=0.0, ge=0)
    max_tokens: int = Field(default=2048, gt=0)
    max_input_chars: int = Field(default=60000, gt=0)


class ModelsConfig(StrictModel):
    main: MainModelConfig = Field(default_factory=MainModelConfig)
    summarizer: SummarizerConfig = Field(default_factory=SummarizerConfig)
    web_reader: WebReaderConfig = Field(default_factory=WebReaderConfig)


class WebConfig(StrictModel):
    searxng_url: str = "http://10.0.0.228:18082"
    allowed_private_hosts: list[str] = Field(default_factory=list)
    user_agent: str = "ai-harness/0.1 (+local)"
    fetch_timeout_s: float = Field(default=30, gt=0)
    max_page_bytes: int = Field(default=5_000_000, gt=0)
    max_redirects: int = Field(default=5, ge=0)
    untrusted_dirs: list[str] = Field(
        default_factory=lambda: [_in_home("downloads"), "~/Downloads"]
    )
    raw_url_max_chars: int = Field(default=20000, gt=0)

    @field_validator("searxng_url")
    @classmethod
    def _check_searx(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("untrusted_dirs")
    @classmethod
    def _expand_dirs(cls, value: list[str]) -> list[str]:
        return [_expand(v) or v for v in value]


class SecurityConfig(StrictModel):
    deny_paths: list[str] = Field(
        default_factory=lambda: [
            "~/.ssh",
            "~/.gnupg",
            "~/.aws",
            "~/.azure",
            "~/.kube/config",
            "~/.netrc",
            "~/.git-credentials",
            "~/.docker/config.json",
            "~/.config/gh",
            _in_home("config.yml"),
            _in_home("sessions.sqlite3"),
            _in_home("secrets"),
        ]
    )
    deny_names: list[str] = Field(
        default_factory=lambda: [
            ".env",
            "id_rsa",
            "id_ed25519",
            "credentials.json",
            "secrets.yml",
            "secrets.yaml",
        ]
    )

    @field_validator("deny_paths")
    @classmethod
    def _expand_paths(cls, value: list[str]) -> list[str]:
        return [_expand(v) or v for v in value]


class SkillsConfig(StrictModel):
    extra_dirs: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    default_timeout_s: float = Field(default=60, gt=0)
    timeouts: dict[str, float] = Field(
        default_factory=lambda: {
            "terminal": 300,
            "background_command": 10,
            "web_fetch": 90,
            "web_search": 60,
            "get_url_raw": 60,
            "pdf_read": 60,
        }
    )
    max_result_chars: int = Field(default=30000, gt=0)

    @field_validator("extra_dirs")
    @classmethod
    def _expand_dirs(cls, value: list[str]) -> list[str]:
        return [_expand(v) or v for v in value]

    def timeout_for(self, skill_name: str) -> float:
        return self.timeouts.get(skill_name, self.default_timeout_s)


class TerminalConfig(StrictModel):
    linux_shell: str = "/bin/bash"
    windows_shell: str = "powershell.exe"
    scrollback_lines: int = Field(default=10000, gt=0)
    font_family: str = "monospace"
    font_size: int = Field(default=12, gt=0)


class HandoffConfig(StrictModel):
    editor: str = "code --goto {path}:{line}"
    file_manager: str | None = None


class LogLevels(StrictModel):
    internal: LogLevel = "INFO"
    prompts: LogLevel = "INFO"
    audit: LogLevel = "INFO"


class LoggingConfig(StrictModel):
    dir: str = Field(default_factory=lambda: _in_home("logs"))
    rollover_mb: float = Field(default=500, gt=0)
    backups: int = Field(default=3, ge=0)
    levels: LogLevels = Field(default_factory=LogLevels)
    console: bool = False

    @field_validator("dir")
    @classmethod
    def _expand_dir(cls, value: str) -> str:
        return _expand(value) or value


class SessionsConfig(StrictModel):
    db_path: str = Field(default_factory=lambda: _in_home("sessions.sqlite3"))
    default_cwd: str | None = None

    @field_validator("db_path", "default_cwd")
    @classmethod
    def _expand_path(cls, value: str | None) -> str | None:
        return _expand(value)


class PromptConfig(StrictModel):
    startup: str = (
        "You are the agent inside a personal AI harness running on the user's own\n"
        "computer. You act through the skills you are given. Be direct and concise.\n"
        "Every result you produce can be handed to the user, so tell them when a\n"
        "handoff would help (for example, opening the terminal or a file).\n"
    )


class ThemeConfig(StrictModel):
    background: str = "#2b3137"
    surface: str = "#343b42"
    surface_alt: str = "#3d454d"
    border: str = "#4a535c"
    text: str = "#e6e9ec"
    text_muted: str = "#a3adb7"
    accent: str = "#7cb342"
    accent_hover: str = "#93c95a"
    accent_text: str = "#101409"
    user_bubble: str = "#3a4a3a"
    assistant_bubble: str = "#343b42"
    warning: str = "#e0a030"
    error: str = "#d9534f"
    code_background: str = "#20252a"

    @field_validator("*")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not (value.startswith("#") and len(value) in (4, 7, 9)):
            raise ValueError("must be a hex color like #7cb342")
        return value


class PanelsConfig(StrictModel):
    dock_width: int = Field(default=64, gt=0)
    side_panel_width: int = Field(default=520, gt=0)
    right_column_width: int = Field(default=320, gt=0)
    explorer_height: int = Field(default=400, gt=0)
    window_width: int = Field(default=1500, gt=0)
    window_height: int = Field(default=950, gt=0)
    start_maximized: bool = True


class UiConfig(StrictModel):
    window_title: str = "AI Harness"
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    font_family: str | None = None
    font_size: int = Field(default=11, gt=0)
    panels: PanelsConfig = Field(default_factory=PanelsConfig)
    file_explorer_root: str = "~"
    file_explorer: Literal["orbit", "tree"] = "orbit"
    file_explorer_show_hidden: bool = False
    critter: Literal["lizard", "turtle", "sloth", "dinosaur", "songbird"] = "lizard"

    @field_validator("file_explorer_root")
    @classmethod
    def _expand_root(cls, value: str) -> str:
        return _expand(value) or value


class Config(StrictModel):
    """The whole configuration file."""

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    ui: UiConfig = Field(default_factory=UiConfig)


def default_config_text() -> str:
    """The commented default config shipped with the package.

    The file is written for ``~/.harness``; when HARNESS_HOME points elsewhere the
    paths in it follow, so the text always matches the schema defaults.
    """
    text = importlib.resources.files("harness").joinpath("default_config.yml").read_text("utf-8")
    home = default_home()
    if home != Path.home() / ".harness":
        text = text.replace("~/.harness", str(home))
    return text


def parse_config(text: str, source: str = "<config>") -> Config:
    """Parse and validate YAML text into a :class:`Config`."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: not valid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: top level must be a mapping of sections")
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, source)) from exc


def _format_validation_error(exc: ValidationError, source: str) -> str:
    lines = [f"{source}: {exc.error_count()} invalid setting(s):"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        if err["type"] == "extra_forbidden":
            message = "unknown setting (check spelling)"
        else:
            message = err["msg"]
        lines.append(f"  - {location}: {message}")
    return "\n".join(lines)


def write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_config_text(), encoding="utf-8")


def load_config(paths: HarnessPaths | None = None, path: Path | None = None) -> Config:
    """Load the config file, creating a commented default on first run (P16)."""
    paths = paths or HarnessPaths()
    config_path = path or paths.config_file
    if not config_path.exists():
        write_default_config(config_path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc
    return parse_config(text, source=str(config_path))


def set_config_value(path: Path, dotted_key: str, value: str) -> None:
    """Change one scalar setting in the config file in place, keeping comments.

    Works on the two-level ``section:`` / ``  key:`` layout the default file uses
    (``ui.critter``, ``handoff.editor`` ...). A missing key is appended to its
    section; a missing section is appended to the file.
    """
    section, _, key = dotted_key.partition(".")
    if not key:
        raise ValueError("expected a section.key name")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = yaml.safe_dump(value, default_flow_style=True).strip().removesuffix("\n...")
    rendered = rendered.strip()
    section_start = next(
        (i for i, line in enumerate(lines) if line.rstrip() == f"{section}:"), None
    )
    if section_start is None:
        lines += ["", f"{section}:", f"  {key}: {rendered}"]
    else:
        end = len(lines)
        for i in range(section_start + 1, len(lines)):
            if lines[i] and not lines[i].startswith((" ", "#")):
                end = i
                break
        for i in range(section_start + 1, end):
            stripped = lines[i].lstrip()
            if (
                lines[i].startswith("  ")
                and not lines[i].startswith("   ")
                and stripped.startswith(f"{key}:")
            ):
                comment = ""
                rest = stripped[len(key) + 1 :]
                if " #" in rest:
                    comment = "  #" + rest.split(" #", 1)[1]
                lines[i] = f"  {key}: {rendered}{comment}"
                break
        else:
            lines.insert(end, f"  {key}: {rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
