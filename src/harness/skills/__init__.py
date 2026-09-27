"""The skill framework: the contract every skill follows, discovery, approval and execution.

To add a skill, drop a ``.py`` file defining one :class:`~harness.skills.base.Skill`
subclass into ``harness/skills/builtin/`` (or ``~/.harness/skills/``). Nothing
else needs to change. See ``docs/skills.md``.
"""

from harness.skills.base import (
    ApprovalDecision,
    ApprovalRequest,
    Handoff,
    Services,
    Skill,
    SkillContext,
    SkillResult,
)
from harness.skills.registry import SkillRegistry
from harness.skills.runner import ApprovalBroker, SkillRunner

__all__ = [
    "ApprovalBroker",
    "ApprovalDecision",
    "ApprovalRequest",
    "Handoff",
    "Services",
    "Skill",
    "SkillContext",
    "SkillResult",
    "SkillRegistry",
    "SkillRunner",
]
