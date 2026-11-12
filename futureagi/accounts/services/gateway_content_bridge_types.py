"""Explicit M1-M7 ports, not substitute accounts authority or ORM models.

Only main-owned production adapters may construct ProvenIdentity / Target.
Mocks are confined to tests. This module provides no positive defaults.
"""

from dataclasses import dataclass
from typing import Callable, Protocol
from uuid import UUID


class PortUnavailable(Exception):
    """Closed dependency failure; never expose an underlying exception."""


@dataclass(frozen=True, repr=False)
class GatewayActor:
    person_id: UUID
    gateway_key_id: str


@dataclass(frozen=True, repr=False)
class ProvenIdentity:
    person_id: UUID
    org_id: UUID
    kind: str
    credential_id: UUID
    pinned_workspace_id: UUID | None


@dataclass(frozen=True, repr=False)
class Request:
    actor: GatewayActor
    org_id: UUID
    workspace_id: UUID
    action: str
    target: str
    phase: str
    request_id: str
    unit_id: str


@dataclass(frozen=True, repr=False)
class Target:
    organization_id: UUID
    kind: str
    server_id: str | None = None
    prompt_names: frozenset[str] | None = None
    prompt_required: frozenset[str] | None = None
    tool_surface: str | None = None


@dataclass(frozen=True, repr=False)
class Decision:
    decision: str
    reason_code: str
    membership_revision: bytes | None


class M1GatewayCredentials(Protocol):
    def resolve(self, transport: object, actor: GatewayActor) -> ProvenIdentity | None:
        """Prove the exact gateway credential; None means inactive/absent.

        Missing bridge/schema is PortUnavailable, never person-first fallback.
        """
        ...


class M2OAuthCredentials(Protocol):
    def resolve(
        self, transport: object, verified_grant: object
    ) -> ProvenIdentity | None:
        """Reserved for approved provenance; v1 cannot supply verified_grant."""
        ...


class M3Workspace(Protocol):
    def resolve(self, identity: ProvenIdentity, requested: UUID) -> UUID | None:
        """Select from trusted routing; canonical still checks fresh membership."""
        ...


class M4Targets(Protocol):
    def resolve(
        self, handle: str, org: UUID, workspace: UUID, kind: str
    ) -> Target | None:
        """Fresh local lookup inside canonical primary transaction. No network."""
        ...


class M5PromptArguments(Protocol):
    def resolve(
        self, identity: ProvenIdentity, request: Request
    ) -> dict[str, str] | None:
        """Approved transient request/unit-bound custody, not wire values.

        None means unavailable. An intentional empty dict is not omission.
        This proposed seam requires main approval before positive prompt use.
        """
        ...


class M6ServiceAuthentication(Protocol):
    def authenticate(self, evidence: bytes) -> object | None:
        """Dedicated reviewed service proof; no user/admin credential fallback."""
        ...


class M7RevisionGeneration(Protocol):
    def read_in_authority_transaction(self, org: UUID, workspace: UUID) -> bytes | None:
        """Main binds this INSIDE canonical authority, not after its decision.

        Must cover all relevant committed writers including ABA transitions.
        No timestamp, random value, state hash or gateway counter substitute.
        """
        ...


class CanonicalAuthority(Protocol):
    def authorize(
        self,
        identity: ProvenIdentity,
        request: Request,
        resolve_target: Callable[[str, UUID, UUID], Target | None],
        arguments: dict[str, str] | None,
    ) -> Decision:
        """Owns roles, lifecycle, fresh primary reads and M7 revision policy."""
        ...


@dataclass(frozen=True)
class Ports:
    transport: M6ServiceAuthentication
    gateway_keys: M1GatewayCredentials
    oauth: M2OAuthCredentials
    workspaces: M3Workspace
    targets: M4Targets
    prompts: M5PromptArguments
    canonical: CanonicalAuthority
