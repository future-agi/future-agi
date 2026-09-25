import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationAttribution,
    TraceInvestigationAttributionEvidence,
    TraceInvestigationEvidenceReceipt,
    TraceInvestigationFinding,
    TraceInvestigationFindingEvidence,
    TraceInvestigationGroupingStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
    TraceInvestigationRequirementCheck,
    TraceInvestigationRequirementEvidence,
    TraceInvestigationSource,
    TraceInvestigationVerificationReceipt,
)
from tracer.queries import grouping
from tracer.queries.grouping import (
    GroupingSnapshotError,
    canonical_grouping_source_digest,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)

pytestmark = pytest.mark.django_db

RECORDED_AT = datetime(2026, 9, 18, 10, 11, 12, 123456, tzinfo=UTC)
READ_CUTOFF = datetime(2026, 9, 18, 10, 10, 0, 1, tzinfo=UTC)


def _saved_report(project, *, identity: str | None = None):
    def record_id(default: str, kind: str) -> uuid.UUID:
        if identity is None:
            return uuid.UUID(default)
        return uuid.uuid5(uuid.NAMESPACE_URL, f"grouping-test:{identity}:{kind}")

    trace_id = record_id("55555555-5555-4555-8555-555555555555", "trace")
    job = TraceInvestigationJob.no_workspace_objects.create(
        id=record_id("66666666-6666-4666-8666-666666666666", "job"),
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project=project,
        trace_id=trace_id,
        root_span_id="0123456789abcdef",
        root_end_time=READ_CUTOFF,
        generation=7,
        state=TraceInvestigationJobState.COMPLETED,
        not_before=READ_CUTOFF,
    )
    attempt = TraceInvestigationAttempt.no_workspace_objects.create(
        id=record_id("77777777-7777-4777-8777-777777777777", "attempt"),
        job=job,
        generation=7,
        worker_id="synthetic-node-worker",
        engine_version="omega-v1",
        status=TraceInvestigationAttemptStatus.COMPLETED,
        lease_token_digest="d" * 64,
        lease_expires_at=READ_CUTOFF + timedelta(minutes=5),
        read_cutoff=READ_CUTOFF,
        memory_snapshot_id="memory-7",
        memory_digest=f"sha256:{'a' * 64}",
        memory=[],
        limits={},
        completed_at=RECORDED_AT,
    )
    report = TraceInvestigationReport.no_workspace_objects.create(
        id=record_id("11111111-1111-4111-8111-111111111111", "report"),
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project=project,
        trace_id=trace_id,
        source=TraceInvestigationSource.OMEGA,
        recorded_at=RECORDED_AT,
        is_current=True,
        has_issues=True,
        job=job,
        attempt=attempt,
        idempotency_key=f"synthetic-publication-7{f'-{identity}' if identity else ''}",
        result_digest=f"sha256:{'b' * 64}",
        contract_version="omega-investigation/v1",
        evidence_digest=f"sha256:{'c' * 64}",
        execution_status="completed",
        outcome="failure",
        coverage_scope="available trace at read cutoff",
        observed_span_count=2,
        read_complete=False,
        future_arrivals_known=True,
        model_calls=2,
        input_tokens=1234,
        output_tokens=56,
        cost_usd=Decimal("0.010000000"),
        cost_status="complete",
        grouping_status=TraceInvestigationGroupingStatus.PENDING,
    )
    job.current_report = report
    job.save(update_fields=["current_report", "updated_at"])

    requirement = TraceInvestigationRequirementCheck.no_workspace_objects.create(
        report=report,
        requirement_id="requirement-1",
        ordinal=0,
        requirement="Refund café customer",
        status="violated",
    )
    receipt = TraceInvestigationEvidenceReceipt.no_workspace_objects.create(
        report=report,
        evidence_id="evidence-1",
        ordinal=0,
        span_id="0123456789abcdef",
        excerpt="requested=100; executed=10",
        end_time=datetime(2026, 9, 18, 10, 9, 59, 999999, tzinfo=UTC),
    )
    deleted_receipt = TraceInvestigationEvidenceReceipt.no_workspace_objects.create(
        report=report,
        evidence_id="deleted-evidence",
        ordinal=1,
        span_id="fedcba9876543210",
        excerpt="deleted excerpt",
    )
    finding = TraceInvestigationFinding.no_workspace_objects.create(
        id=record_id("88888888-8888-4888-8888-888888888888", "finding"),
        report=report,
        finding_id="finding-1",
        ordinal=0,
        kind="outcome",
        statement="Only 10 was refunded instead of 100.",
        recovery="not_observed",
        requirement=requirement,
    )
    TraceInvestigationRequirementEvidence.no_workspace_objects.create(
        requirement=requirement, evidence=receipt
    )
    TraceInvestigationFindingEvidence.no_workspace_objects.create(
        finding=finding, evidence=receipt
    )
    TraceInvestigationFindingEvidence.no_workspace_objects.create(
        finding=finding, evidence=deleted_receipt
    )
    for role, status, span_id in (
        ("origin", "unknown", None),
        ("decisive", "supported", receipt.span_id),
        ("symptom", "unknown", None),
    ):
        attribution = TraceInvestigationAttribution.no_workspace_objects.create(
            finding=finding,
            role=role,
            status=status,
            span_id=span_id,
        )
        if role == "decisive":
            TraceInvestigationAttributionEvidence.no_workspace_objects.create(
                attribution=attribution, evidence=receipt
            )
        if role == "origin":
            attribution.deleted = True
            attribution.save(update_fields=["deleted", "updated_at"])
    TraceInvestigationVerificationReceipt.no_workspace_objects.create(
        report=report,
        receipt_id="verification-1",
        ordinal=0,
        executed=True,
    )
    deleted_receipt.deleted = True
    deleted_receipt.save(update_fields=["deleted", "updated_at"])
    return report


def test_export_grouping_snapshot_preserves_normalized_rows_and_provenance(
    observe_project,
):
    report = _saved_report(observe_project)

    snapshot = export_grouping_snapshot(report=report)

    assert snapshot["contract_version"] == "grouping-snapshot/v1"
    assert snapshot["report"]["id"] == str(report.id)
    assert snapshot["report"]["generation"] == 7
    assert snapshot["report"]["recorded_at"] == "2026-09-18T10:11:12.123456Z"
    assert snapshot["report"]["usage"]["cost_usd"] == "0.010000000"
    assert snapshot["report"]["result_digest"] == f"sha256:{'b' * 64}"
    assert snapshot["report"]["evidence_receipts"] == [
        {
            "evidence_id": "evidence-1",
            "span_id": "0123456789abcdef",
            "parent_span_id": None,
            "excerpt": "requested=100; executed=10",
            "end_time": "2026-09-18T10:09:59.999999Z",
        }
    ]
    assert snapshot["report"]["findings"][0]["evidence_ids"] == [
        "evidence-1",
        "deleted-evidence",
    ]
    assert snapshot["report"]["findings"][0]["attribution"]["decisive"] == {
        "status": "supported",
        "span_id": "0123456789abcdef",
        "evidence_ids": ["evidence-1"],
    }
    assert snapshot["occurrences"] == [
        {
            "occurrence_id": str(report.findings.get().id),
            "finding_id": "finding-1",
        }
    ]
    body = {key: value for key, value in snapshot.items() if key != "snapshot_digest"}
    assert snapshot["snapshot_digest"] == canonical_snapshot_digest(body)
    assert snapshot["snapshot_digest"] != snapshot["report"]["result_digest"]

    # The worker test owns the same fully synthetic projection. Normalizing the
    # three fixture-created tenant IDs proves the whole snapshot, not only a
    # toy object, hashes identically in Python and Node.
    cross_language = deepcopy(snapshot)
    cross_language["report"].update(
        {
            "organization_id": "22222222-2222-4222-8222-222222222222",
            "workspace_id": "33333333-3333-4333-8333-333333333333",
            "project_id": "44444444-4444-4444-8444-444444444444",
        }
    )
    cross_language_body = {
        key: value for key, value in cross_language.items() if key != "snapshot_digest"
    }
    assert canonical_snapshot_digest(cross_language_body) == (
        "sha256:04b4fd6c95b0e8f45d5bb92b365f8b8ec1136036b85c6483fea56a5ab05b6622"
    )
    assert canonical_grouping_source_digest(cross_language) == (
        "sha256:28d4fd92c5b661ff265b0c92ebc243914f5f19e8646c5914a98c3fcb20651123"
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"is_current": False}, "not current"),
        ({"execution_status": "failed"}, "not completed"),
        ({"grouping_status": TraceInvestigationGroupingStatus.STALE}, "stale"),
        ({"workspace_id": None}, "workspace does not match"),
        ({"deleted": True}, "deleted or does not exist"),
    ],
)
def test_export_rejects_ineligible_or_wrong_scope_report(
    observe_project, updates, message
):
    report = _saved_report(observe_project)
    TraceInvestigationReport.no_workspace_objects.filter(id=report.id).update(**updates)

    with pytest.raises(GroupingSnapshotError, match=message):
        export_grouping_snapshot(report=report)


def test_export_fails_instead_of_truncating_children(observe_project, monkeypatch):
    report = _saved_report(observe_project)
    monkeypatch.setattr(grouping, "MAX_FINDINGS", 0)

    with pytest.raises(GroupingSnapshotError, match="findings exceeds"):
        export_grouping_snapshot(report=report)


def test_canonical_digest_matches_node_fixture():
    fixture = {
        "contract_version": "grouping-snapshot/v1",
        "date": "2026-09-18T10:11:12.123456Z",
        "cost_usd": "0.010000000",
        "label": "Refund café customer",
        "ordered": [2, 1],
        "nested": {"z": None, "a": True},
    }

    assert canonical_snapshot_digest(fixture) == (
        "sha256:6ad83376b3874156fc7bbffa67bdbcd14bde430991925d6ea6cdf2603cbf9aaf"
    )
