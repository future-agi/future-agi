"""Severity authority tests: real database writes, no paid model calls."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.trace_grouping import TraceGroupingAttempt, TraceGroupingSeverityJob
from tracer.services.grouping import accounting, severity
from tracer.services.grouping.control import GroupingConflict, GroupingControlError
from tracer.services.grouping.human_edits import edit_grouping_issue
from tracer.services.grouping.publish import _new_issue
from tracer.tests.test_grouping_runtime import _claimed_runtime

pytestmark = pytest.mark.django_db


@pytest.fixture
def assessment(observe_project, monkeypatch):
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=True,
        ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
        ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
        ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
        ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
    ):
        report, claim = _claimed_runtime(observe_project, monkeypatch)
        attempt = TraceGroupingAttempt.no_workspace_objects.select_related(
            "work__scope"
        ).get(pk=claim["attempt_id"])
        finding = report.findings.get()
        issue = _new_issue(
            attempt.work.scope, {"mechanism": "Wrong identifier"}, [str(finding.id)]
        )
        finding.cluster = issue.cluster
        finding.save(update_fields=["cluster"])
        severity.enqueue_severity(issue=issue, attempt=attempt)
        TraceGroupingSeverityJob.no_workspace_objects.update(not_before=timezone.now())
        severity_claim = severity.claim_severity(worker_id="test-severity", limit=1)[
            "claims"
        ][0]
        yield issue, attempt, severity_claim


def _receipt(attempt, claim, grade="high", citations=None):
    evidence = claim["snapshot"]["members"][0]
    item = evidence["evidence"][0]
    result = {
        "severity": grade,
        "reason": "Supported task failure.",
        "citations": citations
        if citations is not None
        else [
            {
                "occurrence_id": evidence["occurrence_id"],
                "ref": item["ref"],
                "digest": item["digest"],
            }
        ],
    }
    args = {
        "attempt_id": attempt.id,
        "severity_job_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "request_key": f"severity:{claim['attempt_id']}:test",
        "request_digest": "sha256:" + "a" * 64,
    }
    receipt = accounting.reserve_call(**args, max_cost_usd="0.10")
    accounting.settle_call(
        **args,
        status="unknown",
        result=result,
        model_used="gemini-3.8-flash",
        input_tokens=100,
        output_tokens=30,
    )
    return uuid.UUID(receipt["receipt_id"])


@pytest.mark.parametrize(
    "grade,priority",
    [("critical", "urgent"), ("high", "high"), ("medium", "medium"), ("low", "low")],
)
def test_assessment_updates_only_severity_and_replays_idempotently(
    assessment, grade, priority
):
    issue, attempt, claim = assessment
    original_status = issue.cluster.status
    receipt = _receipt(attempt, claim, grade)
    args = {
        "job_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "receipt_id": receipt,
    }
    assert severity.publish_severity(**args) == {"status": "completed"}
    assert severity.publish_severity(**args) == {"status": "completed"}
    issue.cluster.refresh_from_db()
    assert issue.cluster.priority == priority
    assert issue.cluster.status == original_status
    assert issue.cluster.severity_source == "llm"
    attempt.work.scope.refresh_from_db()
    assert attempt.work.scope.reserved_usd == Decimal("0.10")


def test_insufficient_evidence_retains_existing_severity(assessment):
    issue, attempt, claim = assessment
    receipt = _receipt(attempt, claim, "insufficient_evidence", [])
    severity.publish_severity(
        job_id=uuid.UUID(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        receipt_id=receipt,
    )
    issue.cluster.refresh_from_db()
    assert issue.cluster.priority == "medium"
    assert issue.cluster.severity_source == "default"
    assert issue.cluster.severity_assessment_status == "insufficient_evidence"


def test_manual_override_and_stale_revision_cannot_be_overwritten(assessment):
    issue, attempt, claim = assessment
    receipt = _receipt(attempt, claim)

    def manual(cluster):
        cluster.priority, cluster.severity_source = "low", "manual"
        return ["priority", "severity_source"]

    edit_grouping_issue(issue.cluster_id, manual)
    with pytest.raises(GroupingConflict):
        severity.publish_severity(
            job_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            receipt_id=receipt,
        )
    issue.cluster.refresh_from_db()
    assert issue.cluster.priority == "low"


def test_fabricated_citations_are_rejected(assessment):
    _, attempt, claim = assessment
    receipt = _receipt(
        attempt,
        claim,
        citations=[
            {"occurrence_id": str(uuid.uuid4()), "ref": "report", "digest": "fake"}
        ],
    )
    with pytest.raises(GroupingConflict):
        severity.publish_severity(
            job_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            receipt_id=receipt,
        )


def test_no_evidence_cannot_be_high(assessment):
    _, attempt, claim = assessment
    receipt = _receipt(attempt, claim, citations=[])
    with pytest.raises(GroupingControlError):
        severity.publish_severity(
            job_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            receipt_id=receipt,
        )


def test_expired_job_reuses_original_unknown_receipt_without_refund(assessment):
    _, attempt, claim = assessment
    receipt_id = _receipt(attempt, claim)
    TraceGroupingSeverityJob.no_workspace_objects.filter(pk=claim["attempt_id"]).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    retry = severity.claim_severity(worker_id="retry", limit=1)["claims"][0]
    receipt = accounting.reserve_call(
        attempt_id=attempt.id,
        severity_job_id=uuid.UUID(retry["attempt_id"]),
        lease_token=retry["lease_token"],
        request_key=f"severity:{retry['attempt_id']}:test",
        request_digest="sha256:" + "a" * 64,
        max_cost_usd="0.10",
    )
    assert receipt["created"] is False
    assert receipt["receipt_id"] == str(receipt_id)
    assert receipt["result"]["severity"] == "high"


def test_budget_exhaustion_prevents_severity_call(assessment):
    _, attempt, claim = assessment
    with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0"):
        with pytest.raises(GroupingConflict):
            _receipt(attempt, claim)


def test_new_revision_supersedes_unclaimed_jobs_and_stales_old_result(assessment):
    issue, attempt, claim = assessment
    receipt = _receipt(attempt, claim)
    issue.revision += 1
    issue.save(update_fields=["revision"])
    severity.enqueue_severity(issue=issue, attempt=attempt)
    issue.revision += 1
    issue.save(update_fields=["revision"])
    severity.enqueue_severity(issue=issue, attempt=attempt)
    assert (
        TraceGroupingSeverityJob.no_workspace_objects.filter(
            issue=issue, state="pending"
        ).count()
        == 1
    )
    with pytest.raises(GroupingConflict):
        severity.publish_severity(
            job_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            receipt_id=receipt,
        )


def test_status_edit_requeues_assessment_without_changing_status(assessment):
    issue, _, _ = assessment

    def acknowledge(cluster):
        cluster.status = "acknowledged"
        return ["status"]

    edit_grouping_issue(issue.cluster_id, acknowledge)
    job = TraceGroupingSeverityJob.no_workspace_objects.get(
        issue=issue, state="pending"
    )
    assert job.issue_revision == issue.revision + 1
    issue.cluster.refresh_from_db()
    assert issue.cluster.status == "acknowledged"


def test_changed_evidence_rejected_even_without_membership_change(assessment):
    issue, attempt, claim = assessment
    receipt = _receipt(attempt, claim)
    finding = issue.cluster.investigation_findings.get()
    finding.statement = "Changed source"
    finding.save(update_fields=["statement"])
    with pytest.raises(GroupingConflict):
        severity.publish_severity(
            job_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            receipt_id=receipt,
        )


def test_oversized_evidence_does_not_fail_grouping_or_change_priority(
    assessment, monkeypatch
):
    issue, _, claim = assessment
    TraceGroupingSeverityJob.no_workspace_objects.filter(pk=claim["attempt_id"]).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    monkeypatch.setattr(severity, "MAX_INPUT_BYTES", 1)
    assert severity.claim_severity(worker_id="retry", limit=1) == {"claims": []}
    issue.cluster.refresh_from_db()
    assert issue.cluster.priority == "medium"
    assert issue.cluster.severity_assessment_status == "failed"


@pytest.mark.django_db(transaction=True)
def test_severity_migration_roundtrip_on_isolated_test_schema():
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    with override_settings(MIGRATION_MODULES={}):
        loader = MigrationLoader(connection)
    migration = loader.get_migration("tracer", "0106_grouping_severity")
    before = loader.project_state([("tracer", "0105_tracegroupingattempt_and_more")])
    # pytest --nomigrations builds the current model schema. Exercise the actual
    # new migration backwards and forwards, not just model-created tables.
    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    with connection.schema_editor() as editor:
        migration.apply(before, editor)
    assert (
        "tracer_trace_grouping_severity_job" in connection.introspection.table_names()
    )
