"""Typed JSON contract for production Error Feed grouping snapshots."""

from typing import Literal, TypedDict

GROUPING_SNAPSHOT_CONTRACT_VERSION = "grouping-snapshot/v1"


class GroupingSnapshotCoverage(TypedDict):
    scope: str
    observed_span_count: int
    read_complete: bool
    future_arrivals_known: bool


class GroupingSnapshotUsage(TypedDict):
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: str | None
    cost_status: str


class GroupingSnapshotRequirement(TypedDict):
    requirement_id: str
    requirement: str
    status: str
    evidence_ids: list[str]


class GroupingSnapshotEvidence(TypedDict):
    evidence_id: str
    span_id: str
    parent_span_id: str | None
    excerpt: str
    end_time: str | None


class GroupingSnapshotAttributionRole(TypedDict):
    status: str
    span_id: str | None
    evidence_ids: list[str]


class GroupingSnapshotAttribution(TypedDict):
    origin: GroupingSnapshotAttributionRole | None
    decisive: GroupingSnapshotAttributionRole | None
    symptom: GroupingSnapshotAttributionRole | None


class GroupingSnapshotFinding(TypedDict):
    finding_id: str
    kind: str | None
    statement: str
    requirement_id: str | None
    evidence_ids: list[str]
    recovery: str | None
    attribution: GroupingSnapshotAttribution


class GroupingSnapshotVerification(TypedDict):
    receipt_id: str
    executed: bool


class GroupingSnapshotReport(TypedDict):
    id: str
    organization_id: str
    workspace_id: str | None
    project_id: str
    trace_id: str
    source: Literal["omega"]
    source_version: str | None
    recorded_at: str
    is_current: bool
    has_issues: bool | None
    grouping_status: str
    job_id: str
    generation: int
    attempt_id: str
    engine_version: str
    read_cutoff: str
    memory_snapshot_id: str
    memory_digest: str
    idempotency_key: str
    source_contract_version: str
    result_digest: str
    evidence_digest: str
    execution_status: Literal["completed"]
    outcome: str
    coverage: GroupingSnapshotCoverage
    usage: GroupingSnapshotUsage
    requirement_checks: list[GroupingSnapshotRequirement]
    evidence_receipts: list[GroupingSnapshotEvidence]
    findings: list[GroupingSnapshotFinding]
    verification_receipts: list[GroupingSnapshotVerification]
    missing_fields: list[str]


class GroupingSnapshotOccurrence(TypedDict):
    occurrence_id: str
    finding_id: str


class GroupingSnapshot(TypedDict):
    contract_version: Literal["grouping-snapshot/v1"]
    report: GroupingSnapshotReport
    occurrences: list[GroupingSnapshotOccurrence]
    snapshot_digest: str
