"""Skill discovery and registration (F1, F2, P6).

Skills are found by importing every ``.py`` file in the skill folders and
collecting the :class:`Skill` subclasses defined there. A file that fails to
import or a class that breaks the contract is reported and skipped; it never
takes the harness down.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.model.types import ToolSpec
from harness.skills.base import EMBEDDED_VIEWS, EXTERNAL_ACTIONS, Skill

log = logging.getLogger(__name__)

HANDOFF_PARAM = "handoff"
HANDOFF_PARAM_SCHEMA = {
    "type": "boolean",
    "description": (
        "Set true when the user asked to see or take over the result themselves "
        "(e.g. 'open the terminal for me'); the harness then performs the handoff immediately."
    ),
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SkillContractError(Exception):
    pass


@dataclass
class LoadProblem:
    source: str
    error: str


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.problems: list[LoadProblem] = []

    # -- registration ------------------------------------------------------

    def register(self, skill_cls: type[Skill]) -> Skill:
        validate_contract(skill_cls)
        skill = skill_cls()
        if skill.name in self._skills:
            raise SkillContractError(
                f"duplicate skill name {skill.name!r} ({skill_cls.__module__})"
            )
        self._skills[skill.name] = skill
        log.info("registered skill %s (%s)", skill.name, skill_cls.__module__)
        return skill

    def register_module(self, module: Any, source: str) -> None:
        found = 0
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Skill) and obj is not Skill and obj.__module__ == module.__name__:
                if inspect.isabstract(obj):
                    continue
                try:
                    self.register(obj)
                    found += 1
                except SkillContractError as exc:
                    self.problems.append(LoadProblem(source, str(exc)))
                    log.error("skill contract error in %s: %s", source, exc)
        if not found:
            log.debug("no skills found in %s", source)

    def load_builtin(self) -> None:
        import harness.skills.builtin as builtin

        for info in pkgutil.iter_modules(builtin.__path__):
            if info.name.startswith("_"):
                continue
            qualified = f"{builtin.__name__}.{info.name}"
            try:
                module = importlib.import_module(qualified)
            except Exception as exc:  # noqa: BLE001 - one broken skill must not stop the rest
                self.problems.append(LoadProblem(qualified, f"{exc.__class__.__name__}: {exc}"))
                log.exception("failed to import built-in skill module %s", qualified)
                continue
            self.register_module(module, qualified)

    def load_directory(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for file in sorted(directory.glob("*.py")):
            if file.name.startswith("_"):
                continue
            module_name = f"harness_user_skill_{file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, file)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                self.problems.append(LoadProblem(str(file), f"{exc.__class__.__name__}: {exc}"))
                log.exception("failed to load skill file %s", file)
                continue
            self.register_module(module, str(file))

    def disable(self, names: list[str]) -> None:
        for name in names:
            if self._skills.pop(name, None) is not None:
                log.info("skill %s disabled by config", name)

    # -- queries -----------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def names(self) -> list[str]:
        return [s.name for s in self.all()]

    def tool_specs(self) -> list[ToolSpec]:
        return [tool_spec_for(skill) for skill in self.all()]


def tool_spec_for(skill: Skill) -> ToolSpec:
    """The model-facing description, with the uniform ``handoff`` parameter added (H3)."""
    schema = dict(skill.parameters)
    props = dict(schema.get("properties", {}))
    props.setdefault(HANDOFF_PARAM, HANDOFF_PARAM_SCHEMA)
    schema["properties"] = props
    description = skill.description.strip()
    if skill.needs_approval:
        description += " (Requires user approval.)"
    description += f" Handoff: {skill.handoff_description.strip()}"
    return ToolSpec(name=skill.name, description=description, parameters=schema)


def validate_contract(skill_cls: type[Skill]) -> None:
    where = f"{skill_cls.__module__}.{skill_cls.__name__}"
    for attr in ("name", "description", "parameters", "handoff_description"):
        if not hasattr(skill_cls, attr):
            raise SkillContractError(f"{where} is missing the {attr!r} declaration")
    name = skill_cls.name
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SkillContractError(
            f"{where}: name must be lowercase letters, digits and underscores, got {name!r}"
        )
    if not isinstance(skill_cls.description, str) or not skill_cls.description.strip():
        raise SkillContractError(f"{where}: description must be a non-empty string")
    if (
        not isinstance(skill_cls.handoff_description, str)
        or not skill_cls.handoff_description.strip()
    ):
        raise SkillContractError(
            f"{where}: handoff_description must say how the result is handed to the user (F3)"
        )
    params = skill_cls.parameters
    if (
        not isinstance(params, dict)
        or params.get("type") != "object"
        or not isinstance(params.get("properties", {}), dict)
    ):
        raise SkillContractError(f"{where}: parameters must be a JSON schema of type 'object'")
    if HANDOFF_PARAM in params.get("properties", {}):
        raise SkillContractError(
            f"{where}: the {HANDOFF_PARAM!r} parameter is added by the harness; do not declare it"
        )
    if inspect.isabstract(skill_cls):
        raise SkillContractError(f"{where} does not implement run()")
    if skill_cls.timeout_s is not None and skill_cls.timeout_s <= 0:
        raise SkillContractError(f"{where}: timeout_s must be positive")


def validate_handoff_target(handoff_kind: str, action: str) -> str | None:
    if handoff_kind == "external" and action not in EXTERNAL_ACTIONS:
        return f"unknown external handoff action {action!r}"
    if handoff_kind == "embedded" and action not in EMBEDDED_VIEWS:
        return f"unknown embedded view {action!r}"
    return None
