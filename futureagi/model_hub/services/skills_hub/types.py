"""Shared service inputs and results; no HTTP or persistence side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from accounts.models.user import OrgApiKey, User
    from model_hub.models.skills_hub import HubSkill, HubSkillVersion


class FileInput(TypedDict):
    path: str
    content: str


@dataclass(frozen=True)
class Actor:
    user: User | None
    api_key: OrgApiKey | None


MISSING = object()


@dataclass
class PublishResult:
    version: HubSkillVersion
    unchanged: bool
    checks: dict


@dataclass
class ResolvedSkill:
    skill: HubSkill
    version: HubSkillVersion
    requested: dict
