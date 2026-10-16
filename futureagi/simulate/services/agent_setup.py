from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class AgentSummary:
    agent_definition_id: UUID
    agent_version_id: UUID | None
    agent_stored: bool
    setup_stale: bool
    provider: str
    provider_agent_id: str
    credential_id: UUID | None
    key_present: bool
    type: str
    kind: str | None
    unit_ids: tuple[str, ...]
    setup_hash: str | None
    provider_info_complete: bool
    missing: tuple[str, ...]


@dataclass(frozen=True)
class SetupOutcome:
    status: str
    failure_code: str
    failure_message: str


class AgentNotFound(Exception):
    pass


class AgentVersionNotFound(Exception):
    pass


class AgentStoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def agent_summary(
    agent_definition_id: UUID, *, agent_version_id: UUID | None = None
) -> AgentSummary:
    raise NotImplementedError("agent_summary is not implemented yet")


def stored_agent(
    agent_version_id: UUID,
    *,
    with_raw_setup: bool = False,
    include_stale: bool = False,
) -> dict | None:
    raise NotImplementedError("stored_agent is not implemented yet")


def store_agent(
    *, agent_version_id: UUID, agent: dict, sdk_version: str
) -> Literal["stored", "unchanged"]:
    raise NotImplementedError("store_agent is not implemented yet")


def store_setup_failure(
    *, agent_version_id: UUID, code: str, message: str
) -> Literal["failed", "ignored"]:
    raise NotImplementedError("store_setup_failure is not implemented yet")


def setup_outcome(agent_version_id: UUID) -> SetupOutcome | None:
    raise NotImplementedError("setup_outcome is not implemented yet")
