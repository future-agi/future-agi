"""Recurrence keeps complete membership and schedules revision-fenced assessment."""

import copy
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tracer.constants.grouping_versions import (
    GROUPING_POLICY_VERSION,
    SAMPLED_GROUPING_POLICY_VERSION,
    SIMULATION_GROUPING_POLICY_VERSION,
)
from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingDecision,
    TraceGroupingScope,
    TraceGroupingSeverityJob,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import InvestigationWorkload
from tracer.services.grouping import context, severity
from tracer.services.grouping.control import (
    GroupingConflict,
    _scope_for_report,
    claim_grouping_work,
)
from tracer.services.grouping.publish import (
    _assign,
    _issue_members,
    _new_issue,
    _recount,
    _text_digest,
    publish_grouping,
)
from tracer.services.grouping_features import enqueue_grouping_features
from tracer.tests.test_grouping_runtime import (
    FakeFeatureStore,
    _prepare_runtime,
    nearest_to,
)
from tracer.tests.test_grouping_severity import _receipt
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def recurrence_settings(settings):
    settings.ERROR_FEED_GROUPING_ENABLED = True
    settings.ERROR_FEED_GROUPING_ALL_PROJECTS = True
    settings.ERROR_FEED_GROUPING_DEBOUNCE_SECONDS = 0
    settings.ERROR_FEED_GROUPING_PROJECT_BUDGET_USD = "10"
    settings.ERROR_FEED_GROUPING_WORK_BUDGET_USD = "10"
    settings.ERROR_FEED_GROUPING_TENANT_BUDGET_USD = "10"


def _create_scope(report, creation_path):
    if creation_path == "enqueue":
        assert enqueue_grouping_features(report=report) is not None
        return TraceGroupingScope.no_workspace_objects.get(project_id=report.project_id)
    return _scope_for_report(report)


@pytest.mark.parametrize("creation_path", ["enqueue", "feature_completion"])
def test_new_trace_scope_uses_sampled_policy(observe_project, creation_path):
    report = _saved_report(observe_project)
    scope = _create_scope(report, creation_path)
    assert scope.policy_version == SAMPLED_GROUPING_POLICY_VERSION
    assert scope.registry_revision == 0


@pytest.mark.parametrize("creation_path", ["enqueue", "feature_completion"])
@pytest.mark.parametrize(
    "policy", [GROUPING_POLICY_VERSION, SAMPLED_GROUPING_POLICY_VERSION]
)
def test_scope_creation_preserves_existing_policy_and_revision(
    observe_project, creation_path, policy
):
    report = _saved_report(observe_project)
    existing = TraceGroupingScope.no_workspace_objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
        policy_version=policy,
        registry_revision=7,
    )
    scope = _create_scope(report, creation_path)
    existing.refresh_from_db()
    assert scope.pk == existing.pk
    assert existing.policy_version == policy
    assert existing.registry_revision == 7
    assert not TraceGroupingDecision.no_workspace_objects.filter(scope=scope).exists()


def test_feature_completion_keeps_simulation_scope_policy(observe_project):
    report = _saved_report(observe_project)
    # Test scope selection only, independently of simulation snapshot serialization.
    report.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    assert (
        _scope_for_report(report).policy_version == SIMULATION_GROUPING_POLICY_VERSION
    )


def _attachment_case(
    project, monkeypatch, *, member_count=1, pending_count=1, policy=None
):
    reports = [
        _prepare_runtime(project, monkeypatch, identity=f"recurrence-existing-{i}")
        for i in range(member_count)
    ]
    scope = TraceGroupingScope.no_workspace_objects.get(project=project)
    if policy is not None:
        scope.policy_version = policy
        scope.save(update_fields=["policy_version", "updated_at"])
    first = reports[0].findings.get()
    mechanism = {
        "title": "Refund uses the wrong amount",
        "mechanism": "Only 10 was refunded instead of 100",
        "fix_hypothesis": "Refund the requested amount",
        "falsifier": "Correct amount refunded",
    }
    issue = _new_issue(scope, mechanism, [str(first.id)])
    for report in reports:
        _assign(report.findings.get(), issue, scope)
    _recount(issue)
    TraceGroupingWork.no_workspace_objects.filter(scope=scope).update(state="completed")
    pending = [
        _prepare_runtime(
            project, monkeypatch, identity=f"recurrence-pending-{i}"
        ).findings.get()
        for i in range(pending_count)
    ]

    class Candidates(FakeFeatureStore):
        candidate_occurrences = nearest_to([str(first.id)])

    monkeypatch.setattr(context, "GroupingFeatureStore", Candidates)
    claim = claim_grouping_work(worker_id="recurrence-test", limit=1)["claims"][0]
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    groups, commands = [], []
    for row in pending:
        citations = [
            {
                "occurrence_id": str(member.id),
                "evidence_id": "report",
                "evidence_digest": _text_digest(member.statement),
                "quote": member.statement[:24],
            }
            for member in (first, row)
        ]
        groups.append(
            {
                "target_issue_id": str(issue.cluster_id),
                "member_ids": [str(row.id)],
                **mechanism,
                "predicted_observations": ["Requested and executed amounts differ"],
                "citations": [
                    {
                        "finding_id": item["occurrence_id"],
                        **{k: v for k, v in item.items() if k != "occurrence_id"},
                    }
                    for item in citations
                ],
                "contradictions": [],
                "alternatives": ["Recognition failure"],
                "missing_evidence": [],
            }
        )
        commands.append(
            {
                "type": "attach",
                "issue_id": str(issue.cluster_id),
                "expected_issue_revision": issue.revision,
                "occurrence_ids": [str(row.id)],
                "citations": citations,
            }
        )
    receipt = TraceGroupingCall.no_workspace_objects.create(
        scope=scope,
        work=attempt.work,
        attempt=attempt,
        request_key="recurrence-attach",
        request_digest="sha256:" + "a" * 64,
        status="settled",
        max_cost_usd=Decimal("0.01"),
        cost_usd=Decimal("0.001"),
        result={"groups": groups, "deferred": []},
    )
    for index, command in enumerate(commands):
        command["admission"] = {
            "primary_receipt_id": str(receipt.pk),
            "group_index": index,
            "repair_receipt_id": None,
        }
    payload = {
        "attempt_id": attempt.pk,
        "lease_token": claim["lease_token"],
        "idempotency_key": "recurrence-publication",
        "snapshot_digest": claim["snapshot_digest"],
        "registry_revision": claim["registry_revision"],
        "receipt_ids": [str(receipt.pk)],
        "commands": commands,
    }
    return SimpleNamespace(
        scope=scope,
        issue=issue,
        first=first,
        pending=pending,
        claim=claim,
        attempt=attempt,
        payload=payload,
    )


def test_default_scope_attaches_eighteenth_occurrence(observe_project, monkeypatch):
    case = _attachment_case(observe_project, monkeypatch, member_count=17)
    offered = case.claim["candidate_window"]["issues"]
    assert [row["issue_id"] for row in offered] == [str(case.issue.cluster_id)]
    assert offered[0]["member_count"] == 17
    assert len(offered[0]["members"]) == 8
    assert not offered[0]["membership_complete"]
    assert len(_issue_members(case.issue)) == 17
    assert not case.claim["candidate_window"]["omitted_candidates"]

    result = publish_grouping(**case.payload)
    case.issue.cluster.refresh_from_db()
    case.pending[0].refresh_from_db()
    assert case.pending[0].cluster_id == case.issue.cluster_id
    assert case.issue.cluster.error_count == 18
    assert len(_issue_members(case.issue)) == 18
    assert (
        ErrorClusterTraces.no_workspace_objects.filter(
            cluster=case.issue.cluster
        ).count()
        == 18
    )
    assert result["created_issue_ids"] == {}


@pytest.mark.parametrize("old_state", ["completed", "running"])
@pytest.mark.parametrize(
    "policy", [GROUPING_POLICY_VERSION, SAMPLED_GROUPING_POLICY_VERSION]
)
def test_attachment_reassesses_once_after_all_commands(
    observe_project, monkeypatch, old_state, policy
):
    case = _attachment_case(
        observe_project, monkeypatch, pending_count=2, policy=policy
    )
    issue = case.issue
    revision, membership_revision = issue.revision, issue.membership_revision
    prototypes = list(issue.prototype_occurrence_ids)
    severity.enqueue_severity(issue=issue, attempt=case.attempt)
    old_job = TraceGroupingSeverityJob.no_workspace_objects.get(issue=issue)
    TraceGroupingSeverityJob.no_workspace_objects.filter(pk=old_job.pk).update(
        not_before=timezone.now()
    )
    old_claim = severity.claim_severity(worker_id="old-assessment", limit=1)["claims"][
        0
    ]
    receipt = _receipt(case.attempt, old_claim)
    old_publication = {
        "job_id": old_job.pk,
        "lease_token": old_claim["lease_token"],
        "receipt_id": receipt,
    }
    if old_state == "completed":
        assert severity.publish_severity(**old_publication) == {"status": "completed"}
    assert old_claim["snapshot"]["member_count"] == 1
    # A no-op refresh shares the original fence with both attach commands.
    case.payload["commands"].append(
        {
            "type": "refresh",
            "issue_id": str(issue.cluster_id),
            "expected_issue_revision": revision,
            "mechanism": issue.mechanism,
            "prototype_occurrence_ids": prototypes,
        }
    )
    result = publish_grouping(**case.payload)
    issue.refresh_from_db()
    issue.cluster.refresh_from_db()
    old_job.refresh_from_db()
    assert issue.revision == revision + 1
    assert issue.membership_revision == membership_revision + 2
    assert issue.prototype_occurrence_ids == prototypes
    assert issue.cluster.error_count == 3
    assert issue.cluster.severity_assessment_status == "pending"
    assert old_job.state == ("completed" if old_state == "completed" else "superseded")
    assert old_job.snapshot == old_claim["snapshot"]
    jobs = TraceGroupingSeverityJob.no_workspace_objects.filter(issue=issue)
    assert jobs.count() == 2
    replacement = jobs.get(issue_revision=revision + 1)
    assert replacement.state == "pending"
    assert replacement.source_attempt_id == case.attempt.pk
    if old_state == "running":
        with pytest.raises(GroupingConflict, match="severity result is stale"):
            severity.publish_severity(**old_publication)

    TraceGroupingSeverityJob.no_workspace_objects.filter(pk=replacement.pk).update(
        not_before=timezone.now()
    )
    fresh = severity.claim_severity(worker_id="new-assessment", limit=1)["claims"][0]
    assert uuid.UUID(fresh["attempt_id"]) == replacement.pk
    assert fresh["snapshot"]["issue_revision"] == revision + 1
    assert fresh["snapshot"]["member_count"] == 3
    assert fresh["snapshot"]["trace_count"] == 3
    assert {row["occurrence_id"] for row in fresh["snapshot"]["members"]} == {
        str(row.pk) for row in [case.first, *case.pending]
    }
    assert publish_grouping(**case.payload) == result
    issue.refresh_from_db()
    assert issue.revision == revision + 1
    assert jobs.count() == 2
    assert (
        TraceGroupingDecision.no_workspace_objects.filter(attempt=case.attempt).count()
        == 1
    )


@pytest.mark.parametrize("failure", ["duplicate", "missing"])
def test_invalid_attachment_rolls_back_then_valid_retry_reassesses(
    observe_project, monkeypatch, failure
):
    case = _attachment_case(observe_project, monkeypatch, pending_count=2)
    revision = case.issue.revision
    membership_revision = case.issue.membership_revision
    severity.enqueue_severity(issue=case.issue, attempt=case.attempt)
    old_job = TraceGroupingSeverityJob.no_workspace_objects.get(issue=case.issue)
    invalid = copy.deepcopy(case.payload)
    if failure == "duplicate":
        invalid["commands"].append(
            {
                "type": "defer",
                "occurrence_ids": [str(case.pending[0].pk)],
                "reason": "duplicate disposition",
            }
        )
        message = "duplicated or not pending"
    else:
        invalid["commands"].pop()
        message = "silently dropped or duplicated"
    with pytest.raises(GroupingConflict, match=message):
        publish_grouping(**invalid)
    case.issue.refresh_from_db()
    case.issue.cluster.refresh_from_db()
    old_job.refresh_from_db()
    assert case.issue.revision == revision
    assert case.issue.membership_revision == membership_revision
    assert case.issue.cluster.error_count == 1
    assert old_job.state == "pending"
    assert (
        TraceGroupingSeverityJob.no_workspace_objects.filter(issue=case.issue).count()
        == 1
    )
    assert _issue_members(case.issue) == [str(case.first.pk)]
    for row in case.pending:
        row.refresh_from_db()
        assert row.cluster_id is None
        assert not ErrorClusterTraces.no_workspace_objects.filter(finding=row).exists()
    assert not TraceGroupingDecision.no_workspace_objects.filter(
        attempt=case.attempt
    ).exists()
    assert publish_grouping(**case.payload)["status"] == "completed"
    case.issue.refresh_from_db()
    old_job.refresh_from_db()
    assert case.issue.revision == revision + 1
    assert old_job.state == "superseded"
    assert TraceGroupingSeverityJob.no_workspace_objects.filter(
        issue=case.issue, issue_revision=revision + 1, state="pending"
    ).exists()


def test_attachment_preserves_manual_severity_without_new_job(
    observe_project, monkeypatch
):
    case = _attachment_case(observe_project, monkeypatch)
    issue = case.issue
    revision = issue.revision
    issue.cluster.priority = "low"
    issue.cluster.severity_source = "manual"
    issue.cluster.severity_reason = "Operator assessment"
    issue.cluster.fix_layer = "Tools"
    issue.cluster.severity_assessment_status = "completed"
    issue.cluster.save(
        update_fields=[
            "priority",
            "severity_source",
            "severity_reason",
            "fix_layer",
            "severity_assessment_status",
            "updated_at",
        ]
    )
    publish_grouping(**case.payload)
    issue.refresh_from_db()
    issue.cluster.refresh_from_db()
    assert issue.revision == revision + 1
    assert issue.cluster.error_count == 2
    assert issue.cluster.priority == "low"
    assert issue.cluster.severity_source == "manual"
    assert issue.cluster.severity_reason == "Operator assessment"
    assert issue.cluster.fix_layer == "Tools"
    assert issue.cluster.severity_assessment_status == "completed"
    assert not TraceGroupingSeverityJob.no_workspace_objects.filter(
        issue=issue
    ).exists()
