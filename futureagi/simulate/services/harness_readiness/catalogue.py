"""What run readiness reads: its rows, their panel areas and gates, and its shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
    HostedHarnessJob,
)

CATALOG_VERSION = "1"
RULES_VERSION = "8"

Status = Literal["supported", "partial", "blocked", "unavailable", "untested", "stale"]
EvidenceSource = Literal["pair_certificate", "build_certificate", "none"]
Finding = Literal["passed", "failed", "limited", "not_applicable", "not_run"]
# The four kinds of repair a blocker sends the user to.
FixCategory = Literal["startup", "message_mapping", "tool_routing", "evidence"]
# An area, and the answer, read as their worst row.
WORST_FIRST: tuple[Status, ...] = (
    "blocked",
    "stale",
    "untested",
    "partial",
    "unavailable",
    "supported",
)
GATE_OPEN = frozenset({"supported", "partial"})


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    # The Ready to run panel line it shows on; ``None`` keeps it off the panel.
    area: str | None
    measured: bool
    # Read only once this row is Supported or Partial.
    gate: str | None = None


CATALOGUE: tuple[Capability, ...] = (
    Capability("start", "Agent starts", "start_and_message", True),
    Capability("message", "Takes a test message", "start_and_message", True, "start"),
    Capability("continuity", "Keeps context across turns", "start_and_message", False),
    Capability("streaming", "Streams its replies", "start_and_message", False),
    Capability("tool_discovery", "Tools discovered", "tool_binding", True, "start"),
    Capability(
        "tool_routing",
        "Controlled tool routing",
        "tool_binding",
        True,
        "tool_discovery",
    ),
    Capability("state", "Seeded world state", "evidence", True, "start"),
    Capability("audio", "Audio evidence", "evidence", False),
    Capability("files", "File evidence", "evidence", False),
    Capability("browser", "Browser evidence", "evidence", False),
    Capability("children", "Sub-agent evidence", "evidence", False),
    Capability(
        "hidden_material", "Grading material kept from the agent", "evidence", False
    ),
    Capability("reset", "World resets between cases", "reset", True, "state"),
    Capability("resume", "Resumes an interrupted case", "reset", False),
    Capability("cancel", "Stops cleanly when cancelled", "cancellation", True),
    Capability("cost", "Run cost", None, False),
)
AREAS = ("start_and_message", "tool_binding", "evidence", "reset", "cancellation")


class VersionRef(TypedDict):
    id: str
    label: str | None


class AgentToolsView(TypedDict):
    # Whether the agent version's tools were read, and how many it has.
    read: bool
    count: int


class PairingView(TypedDict):
    agent_version: VersionRef | None
    environment_version: VersionRef | None
    suite_version: VersionRef | None
    evidence: EvidenceSource
    agent_tools: AgentToolsView
    # The certification archive could not be read: its rows read as unmeasured.
    evidence_unreadable: bool


class AnswerView(TypedDict):
    kind: Literal["ready", "blocked", "stale", "unchecked", "validating"]
    worst: Status
    reason_code: str | None


class AreaView(TypedDict):
    id: str
    status: Status
    rows: list[str]


class Target(TypedDict):
    # ``agent_tool``: a tool of the agent version the environment version lacks.
    kind: Literal["tool", "agent_tool", "action", "check"]
    name: str
    result: str


class EvidenceCheck(TypedDict):
    check: str
    result: str


class FixRecheck(TypedDict):
    scope: Literal["agent", "pair"]
    method: Literal["POST"]
    path: str


class Fix(TypedDict):
    type: str
    category: FixCategory | None
    target: str | None
    action: str
    would: str
    # None when checking this pairing again cannot change the row.
    recheck: FixRecheck | None


class RowView(TypedDict):
    id: str
    label: str
    area: str | None
    status: Status
    measured: bool
    finding: Finding | None
    reason_code: str | None
    reason: str | None
    targets: list[Target]
    blocked_by: str | None
    stale_because: str | None
    was: Status | None
    evidence: list[EvidenceCheck]
    checked_at: str | None
    fix: Fix | None
    # What the status means for the cases, their spend and their independence.
    consequence: str


@dataclass(frozen=True)
class Pairing:
    """An agent, environment and suite version of one environment, read for readiness.

    ``runtime`` is the agent's side row as it runs in ``environment`` (a shared legacy
    row stands for the environment's own facts), or ``None`` without one.
    """

    environment: HostedHarnessJob
    agent_version: AgentVersion | None
    environment_version: HarnessEnvironmentVersion
    suite_version: HarnessSuiteVersion
    runtime: HarnessAgentVersionRuntime | None


@dataclass(frozen=True)
class Reading:
    """What the evidence says about one measured row, before the rules weigh it."""

    finding: Finding
    reason_code: str | None = None
    reason: str | None = None
    targets: list[Target] = field(default_factory=list)
    evidence: list[EvidenceCheck] = field(default_factory=list)
    # When it was observed, for a row not read from the certificate.
    checked_at: str | None = None


def targets(
    kind: Literal["tool", "agent_tool", "action", "check"],
    names: list[str],
    result: str,
) -> list[Target]:
    return [{"kind": kind, "name": name, "result": result} for name in names]
