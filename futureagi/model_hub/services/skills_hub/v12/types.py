"""Shared foundation values. Context credentials are never human principals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

ResourceKind = Literal["skill", "agent_context"]


class LabelSelector(TypedDict):
    label: str


class VersionSelector(TypedDict):
    version: int


Selector = LabelSelector | VersionSelector


class LinkInput(TypedDict):
    skill_id: str
    selector: Selector


class LockedLink(TypedDict):
    mount: str
    skill_id: str
    requested: Selector
    version: int
    version_id: str
    digest: str


class ContextSpec(TypedDict):
    schema: Literal[1]
    description: str
    links: list[LinkInput]


class ResolvedLock(TypedDict):
    schema: Literal[1]
    links: list[LockedLink]


class ConsumeGrant(TypedDict):
    kind: ResourceKind
    resource_id: str
    labels: list[str]
    version_ids: list[str]


class CredentialRequest(TypedDict):
    purpose: Literal["consume", "report"]
    grants: list[ConsumeGrant]
    binding_ids: list[str]
    expires_at: datetime


@dataclass(frozen=True)
class ContextCredentialPrincipal:
    scope: SkillScope
    credential_id: UUID
    purpose: Literal["consume", "report"]

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


ContextPrincipal = SkillPrincipal | ContextCredentialPrincipal


@dataclass(frozen=True)
class CompiledContext:
    files: list[FileInput]
    spec: ContextSpec
    lock: ResolvedLock
    descriptor: dict
    digest: str
    checks: dict


class EvidenceRef(TypedDict):
    schema: Literal[1]
    source: Literal["skills_text", "simulation_native"]
    source_id: str
    subject_kind: ResourceKind
    subject_id: str
    candidate_digest: str
    execution_fingerprint: str
    status: str
    visibility: Literal["private", "workspace"]
    payload_expires_at: str
    configuration: dict
    annotation_revision: int
