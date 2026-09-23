"""Bounded, tenant-scoped reads for the Error Feed grouping boundary."""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar

from django.db.models import QuerySet

from tracer.models.trace_investigation import (
    InvestigationWorkload,
    TraceInvestigationAttribution,
    TraceInvestigationAttributionEvidence,
    TraceInvestigationEvidenceReceipt,
    TraceInvestigationFinding,
    TraceInvestigationFindingEvidence,
    TraceInvestigationGroupingStatus,
    TraceInvestigationReport,
    TraceInvestigationRequirementCheck,
    TraceInvestigationRequirementEvidence,
    TraceInvestigationSource,
    TraceInvestigationVerificationReceipt,
)
from tracer.types.grouping_types import (
    GROUPING_SNAPSHOT_CONTRACT_VERSION,
    GroupingSnapshot,
    GroupingSnapshotAttribution,
    GroupingSnapshotAttributionRole,
    GroupingSnapshotEvidence,
    GroupingSnapshotFinding,
    GroupingSnapshotOccurrence,
    GroupingSnapshotReport,
    GroupingSnapshotRequirement,
    GroupingSnapshotVerification,
)

MAX_FINDINGS = 100
GROUPABLE_RECOVERY = ("unrecovered", "not_recovered", "not_observed", "none")
MAX_REQUIREMENTS = 100
MAX_EVIDENCE_RECEIPTS = 200
MAX_VERIFICATION_RECEIPTS = 100
MAX_ATTRIBUTIONS = MAX_FINDINGS * 3
MAX_FINDING_EVIDENCE_LINKS = MAX_FINDINGS * 100
MAX_REQUIREMENT_EVIDENCE_LINKS = MAX_REQUIREMENTS * 100
MAX_ATTRIBUTION_EVIDENCE_LINKS = MAX_ATTRIBUTIONS * 100
ATTRIBUTION_ROLES = ("origin", "decisive", "symptom")
NORMALIZED_MISSING_FIELDS = (
    "evidence_receipts.input",
    "evidence_receipts.output",
    "investigation_history",
    "requirement_checks.expected",
    "requirement_checks.observed",
    "terminal_effect_mapping",
)

T = TypeVar("T")
SHA256_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


class GroupingSnapshotError(ValueError):
    """The selected report cannot safely cross the grouping boundary."""


def groupable_findings(
    report: TraceInvestigationReport,
) -> QuerySet[TraceInvestigationFinding]:
    """Only unresolved, evidenced task failures become Feed occurrences."""
    findings = TraceInvestigationFinding.no_workspace_objects.filter(report=report)
    if report.execution_status != "completed" or report.outcome != "failure":
        return findings.none()
    return findings.filter(
        recovery__in=GROUPABLE_RECOVERY, requirement__status="violated"
    )


def canonical_snapshot_digest(value: Mapping[str, object]) -> str:
    """Digest canonical JSON shared with the Node grouping adapter.

    Object keys are sorted recursively by ``json.dumps``; array order is
    retained. Snapshot values intentionally contain no floats: dates and
    decimal costs are normalized to strings before reaching this function.
    """

    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def canonical_grouping_source_digest(snapshot: Mapping[str, object]) -> str:
    """Stable source identity excluding only mutable grouping projection state."""
    report = dict(snapshot["report"])
    report.pop("grouping_status", None)
    return canonical_snapshot_digest(
        {
            "contract_version": snapshot["contract_version"],
            "report": report,
            "occurrences": snapshot["occurrences"],
        }
    )


def _bounded(queryset: QuerySet[T], *, limit: int, label: str) -> list[T]:
    rows = list(queryset[: limit + 1])
    if len(rows) > limit:
        raise GroupingSnapshotError(f"{label} exceeds the snapshot bound of {limit}")
    return rows


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise GroupingSnapshotError("snapshot datetime is not timezone-aware")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _cost_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value < 0:
        raise GroupingSnapshotError("snapshot cost_usd cannot be negative")
    return format(value, ".9f")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise GroupingSnapshotError(f"eligible Omega report is missing {label}")
    return value


def _require_digest(value: object, label: str) -> str:
    value = _require_text(value, label)
    if SHA256_DIGEST.fullmatch(value) is None:
        raise GroupingSnapshotError(f"eligible Omega report has invalid {label}")
    return value


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GroupingSnapshotError(f"eligible Omega report has invalid {label}")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise GroupingSnapshotError(f"eligible Omega report has invalid {label}")
    return value


def _validate_report_scope(report: TraceInvestigationReport) -> None:
    project = report.project
    job = report.job
    attempt = report.attempt
    if project.deleted:
        raise GroupingSnapshotError("report project is deleted")
    if report.organization_id != project.organization_id:
        raise GroupingSnapshotError("report organization does not match project scope")
    if report.workspace_id != project.workspace_id:
        raise GroupingSnapshotError("report workspace does not match project scope")
    if project.workspace_id is not None:
        if project.workspace.deleted:
            raise GroupingSnapshotError("report workspace is deleted")
        if project.workspace.organization_id != project.organization_id:
            raise GroupingSnapshotError(
                "project workspace does not match organization scope"
            )
    if job is None or attempt is None:
        raise GroupingSnapshotError("Omega report is missing job/attempt provenance")
    if job.deleted or attempt.deleted:
        raise GroupingSnapshotError("Omega report job/attempt provenance is deleted")
    if (
        job.organization_id != report.organization_id
        or job.workspace_id != report.workspace_id
        or job.project_id != report.project_id
        or job.trace_id != report.trace_id
        or job.workload_type != report.workload_type
        or job.test_execution_id != report.test_execution_id
    ):
        raise GroupingSnapshotError("report job does not match report scope")
    if attempt.job_id != job.id:
        raise GroupingSnapshotError("report attempt does not match report job")
    if job.current_report_id != report.id:
        raise GroupingSnapshotError("report is not the job's current report")


def _group_citations(
    rows: Iterable[tuple[object, str, object]],
    *,
    report_id: object,
    label: str,
) -> dict[object, list[str]]:
    citations: dict[object, list[str]] = {}
    for parent_id, evidence_id, evidence_report_id in rows:
        if evidence_report_id != report_id:
            raise GroupingSnapshotError(f"{label} contains a cross-report citation")
        citations.setdefault(parent_id, []).append(evidence_id)
    return citations


def export_grouping_snapshot(*, report: TraceInvestigationReport) -> GroupingSnapshot:
    """Export one current normalized Omega report for the shared Node adapter.

    The original wire ``result_digest`` is retained only as provenance. The
    returned ``snapshot_digest`` binds this independently normalized payload.
    Every relation is ordered and read with a hard bound; nothing is truncated.
    """

    if report.pk is None:
        raise GroupingSnapshotError("report must be persisted")
    try:
        report = (
            TraceInvestigationReport.no_workspace_objects.select_related(
                "project__organization", "project__workspace", "job", "attempt"
            )
            .filter(id=report.pk)
            .get()
        )
    except TraceInvestigationReport.DoesNotExist as exc:
        raise GroupingSnapshotError("report is deleted or does not exist") from exc

    if report.source != TraceInvestigationSource.OMEGA:
        raise GroupingSnapshotError("only Omega reports can be grouped")
    if not report.is_current:
        raise GroupingSnapshotError("report is not current")
    if report.execution_status != "completed":
        raise GroupingSnapshotError("report execution is not completed")
    if report.grouping_status == TraceInvestigationGroupingStatus.STALE:
        raise GroupingSnapshotError("stale report cannot be grouped")
    _validate_report_scope(report)

    requirements = _bounded(
        TraceInvestigationRequirementCheck.no_workspace_objects.filter(
            report_id=report.id
        ).order_by("ordinal", "id"),
        limit=MAX_REQUIREMENTS,
        label="requirement checks",
    )
    receipts = _bounded(
        TraceInvestigationEvidenceReceipt.no_workspace_objects.filter(
            report_id=report.id
        ).order_by("ordinal", "id"),
        limit=MAX_EVIDENCE_RECEIPTS,
        label="evidence receipts",
    )
    findings = _bounded(
        groupable_findings(report)
        .select_related("requirement")
        .order_by("ordinal", "id"),
        limit=MAX_FINDINGS,
        label="findings",
    )
    verifications = _bounded(
        TraceInvestigationVerificationReceipt.no_workspace_objects.filter(
            report_id=report.id
        ).order_by("ordinal", "id"),
        limit=MAX_VERIFICATION_RECEIPTS,
        label="verification receipts",
    )

    finding_ids = [item.id for item in findings]
    requirement_ids = [item.id for item in requirements]
    attributions = _bounded(
        TraceInvestigationAttribution.no_workspace_objects.filter(
            finding_id__in=finding_ids
        ).order_by("finding__ordinal", "role", "id"),
        limit=MAX_ATTRIBUTIONS,
        label="attributions",
    )
    attribution_ids = [item.id for item in attributions]

    finding_citation_rows = _bounded(
        TraceInvestigationFindingEvidence.no_workspace_objects.filter(
            finding_id__in=finding_ids
        )
        .order_by("finding__ordinal", "evidence__ordinal", "evidence_id")
        .values_list("finding_id", "evidence__evidence_id", "evidence__report_id"),
        limit=MAX_FINDING_EVIDENCE_LINKS,
        label="finding evidence links",
    )
    requirement_citation_rows = _bounded(
        TraceInvestigationRequirementEvidence.no_workspace_objects.filter(
            requirement_id__in=requirement_ids
        )
        .order_by("requirement__ordinal", "evidence__ordinal", "evidence_id")
        .values_list("requirement_id", "evidence__evidence_id", "evidence__report_id"),
        limit=MAX_REQUIREMENT_EVIDENCE_LINKS,
        label="requirement evidence links",
    )
    attribution_citation_rows = _bounded(
        TraceInvestigationAttributionEvidence.no_workspace_objects.filter(
            attribution_id__in=attribution_ids
        )
        .order_by(
            "attribution__finding__ordinal",
            "attribution__role",
            "evidence__ordinal",
            "evidence_id",
        )
        .values_list("attribution_id", "evidence__evidence_id", "evidence__report_id"),
        limit=MAX_ATTRIBUTION_EVIDENCE_LINKS,
        label="attribution evidence links",
    )

    finding_citations = _group_citations(
        finding_citation_rows,
        report_id=report.id,
        label="finding evidence",
    )
    requirement_citations = _group_citations(
        requirement_citation_rows,
        report_id=report.id,
        label="requirement evidence",
    )
    attribution_citations = _group_citations(
        attribution_citation_rows,
        report_id=report.id,
        label="attribution evidence",
    )

    requirement_payload: list[GroupingSnapshotRequirement] = [
        {
            "requirement_id": item.requirement_id,
            "requirement": item.requirement,
            "status": item.status,
            "evidence_ids": requirement_citations.get(item.id, []),
        }
        for item in requirements
    ]
    receipt_payload: list[GroupingSnapshotEvidence] = [
        {
            "evidence_id": item.evidence_id,
            "span_id": item.span_id,
            "parent_span_id": item.parent_span_id,
            **(
                {"call_execution_id": str(item.call_execution_id)}
                if item.call_execution_id
                else {}
            ),
            "excerpt": item.excerpt,
            "end_time": _utc_text(item.end_time),
        }
        for item in receipts
    ]

    attribution_by_finding: dict[object, GroupingSnapshotAttribution] = {
        item.id: {"origin": None, "decisive": None, "symptom": None}
        for item in findings
    }
    for attribution in attributions:
        if attribution.role not in ATTRIBUTION_ROLES:
            raise GroupingSnapshotError("finding contains an unknown attribution role")
        roles = attribution_by_finding[attribution.finding_id]
        if roles[attribution.role] is not None:
            raise GroupingSnapshotError("finding contains a duplicate attribution role")
        role_payload: GroupingSnapshotAttributionRole = {
            "status": attribution.status,
            "span_id": attribution.span_id,
            **(
                {"call_execution_id": str(attribution.call_execution_id)}
                if attribution.call_execution_id
                else {}
            ),
            "evidence_ids": attribution_citations.get(attribution.id, []),
        }
        roles[attribution.role] = role_payload

    finding_payload: list[GroupingSnapshotFinding] = []
    occurrences: list[GroupingSnapshotOccurrence] = []
    for finding in findings:
        requirement_id = None
        if finding.requirement_id is not None:
            if (
                finding.requirement.deleted
                or finding.requirement.report_id != report.id
            ):
                raise GroupingSnapshotError(
                    "finding links to a deleted or cross-report requirement"
                )
            requirement_id = finding.requirement.requirement_id
        finding_payload.append(
            {
                "finding_id": finding.finding_id,
                "kind": finding.kind,
                "statement": finding.statement,
                "requirement_id": requirement_id,
                "evidence_ids": finding_citations.get(finding.id, []),
                "recovery": finding.recovery,
                "attribution": attribution_by_finding[finding.id],
            }
        )
        occurrences.append(
            {"occurrence_id": str(finding.id), "finding_id": finding.finding_id}
        )

    verification_payload: list[GroupingSnapshotVerification] = [
        {"receipt_id": item.receipt_id, "executed": item.executed}
        for item in verifications
    ]
    attempt = report.attempt
    simulation = report.workload_type == InvestigationWorkload.SIMULATION_TEST_EXECUTION
    report_payload: GroupingSnapshotReport = {
        "id": str(report.id),
        "organization_id": str(report.organization_id),
        "workspace_id": str(report.workspace_id) if report.workspace_id else None,
        "project_id": str(report.project_id),
        "trace_id": str(report.trace_id) if report.trace_id else None,
        **(
            {
                "workload_type": InvestigationWorkload.SIMULATION_TEST_EXECUTION,
                "test_execution_id": str(report.test_execution_id),
            }
            if simulation
            else {}
        ),
        "source": "omega",
        "source_version": report.source_version,
        "recorded_at": _utc_text(report.recorded_at),
        "is_current": report.is_current,
        "has_issues": report.has_issues,
        "grouping_status": report.grouping_status,
        "job_id": str(report.job_id),
        "generation": attempt.generation,
        "attempt_id": str(attempt.id),
        "engine_version": _require_text(attempt.engine_version, "engine_version"),
        "read_cutoff": _utc_text(attempt.read_cutoff),
        "memory_snapshot_id": _require_text(
            attempt.memory_snapshot_id, "memory_snapshot_id"
        ),
        "memory_digest": _require_digest(attempt.memory_digest, "memory_digest"),
        "idempotency_key": _require_text(report.idempotency_key, "idempotency_key"),
        "source_contract_version": _require_text(
            report.contract_version, "source contract_version"
        ),
        "result_digest": _require_digest(report.result_digest, "result_digest"),
        "evidence_digest": _require_digest(report.evidence_digest, "evidence_digest"),
        "execution_status": "completed",
        "outcome": _require_text(report.outcome, "outcome"),
        "coverage": {
            "scope": _require_text(report.coverage_scope, "coverage scope"),
            **(
                {
                    "observed_call_count": _require_nonnegative_int(
                        report.observed_call_count, "observed_call_count"
                    )
                }
                if simulation
                else {
                    "observed_span_count": _require_nonnegative_int(
                        report.observed_span_count, "observed_span_count"
                    ),
                    "future_arrivals_known": _require_bool(
                        report.future_arrivals_known, "future_arrivals_known"
                    ),
                }
            ),
            "read_complete": _require_bool(report.read_complete, "read_complete"),
        },
        "usage": {
            "model_calls": _require_nonnegative_int(report.model_calls, "model_calls"),
            "input_tokens": _require_nonnegative_int(
                report.input_tokens, "input_tokens"
            ),
            "output_tokens": _require_nonnegative_int(
                report.output_tokens, "output_tokens"
            ),
            "cost_usd": _cost_text(report.cost_usd),
            "cost_status": _require_text(report.cost_status, "cost_status"),
        },
        "requirement_checks": requirement_payload,
        "evidence_receipts": receipt_payload,
        "findings": finding_payload,
        "verification_receipts": verification_payload,
        "missing_fields": list(NORMALIZED_MISSING_FIELDS),
    }
    body: dict[str, object] = {
        "contract_version": GROUPING_SNAPSHOT_CONTRACT_VERSION,
        "report": report_payload,
        "occurrences": occurrences,
    }
    return {
        "contract_version": GROUPING_SNAPSHOT_CONTRACT_VERSION,
        "report": report_payload,
        "occurrences": occurrences,
        "snapshot_digest": canonical_snapshot_digest(body),
    }
