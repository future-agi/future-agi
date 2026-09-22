"""Local PostgreSQL F6 authority-boundary tests; no CH/model calls."""

import copy
import hashlib
import uuid
from decimal import Decimal

import pytest
from django.test import override_settings

from tracer.constants.grouping_versions import FEATURE_POLICY_VERSION
from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingDecision,
    TraceGroupingFeature,
    TraceGroupingOutbox,
    TraceGroupingScope,
    TraceGroupingSeverityJob,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import (
    TraceInvestigationFinding,
    TraceInvestigationGroupingStatus,
)
from tracer.queries.grouping import (
    canonical_grouping_source_digest,
    canonical_snapshot_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping import context
from tracer.services.grouping.accounting import reserve_call, settle_call
from tracer.services.grouping.control import (
    GroupingConflict,
    checkpoint_attempt,
    claim_feature_jobs,
    claim_grouping_work,
    update_grouping_attempt,
)
from tracer.services.grouping.feature_completion import (
    _text_digest,
    _view_texts,
    complete_feature_job,
)
from tracer.services.grouping.lifecycle import deproject_superseded_report
from tracer.services.grouping.publish import (
    _admitted_group,
    _new_issue,
    publish_grouping,
)
from tracer.services.grouping_features import enqueue_grouping_features
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = pytest.mark.django_db


class FakeFeatureStore:
    def write_and_verify(self, **kwargs):
        assert kwargs["rows"]

    def candidate_occurrences(self, **kwargs):
        return []

    def read_vectors(self, **kwargs):
        return [
            {
                **row,
                "vector": [1.0] * 384,
                "dimension": 384,
                "model": "all-MiniLM-L6-v2",
                "model_revision": None,
                "serving_release": "test-release",
            }
            for row in kwargs["receipts"]
        ]


def _feature_rows(snapshot):
    source = canonical_grouping_source_digest(snapshot)
    return [
        {
            "occurrence_id": occurrence_id,
            "view": view,
            "source_digest": source,
            "evidence_revision": snapshot["report"]["evidence_digest"],
            "text_digest": _text_digest(text),
            "feature_digest": hashlib.sha256(
                f"{occurrence_id}:{view}".encode()
            ).hexdigest(),
            "model": "all-MiniLM-L6-v2",
            "model_revision": None,
            "serving_release": "test-release",
            "dimension": 384,
            "vector": [1.0] * 384,
            "index_buckets": [
                {"table": index, "signature": index} for index in range(8)
            ],
        }
        for (occurrence_id, view), text in _view_texts(snapshot).items()
    ]


def _claimed_runtime(project, monkeypatch):
    report = _saved_report(project)
    job = enqueue_grouping_features(report=report)
    assert job.policy_version == FEATURE_POLICY_VERSION
    feature_claim = claim_feature_jobs(worker_id="test-feature-worker", limit=1)[
        "claims"
    ][0]
    snapshot = feature_claim["snapshot"]
    prepared = complete_feature_job(
        feature_job_id=uuid.UUID(feature_claim["feature_job_id"]),
        lease_token=feature_claim["lease_token"],
        status="ready",
        features=_feature_rows(snapshot),
        store=FakeFeatureStore(),
    )
    assert prepared["state"] == "ready"
    monkeypatch.setattr(context, "GroupingFeatureStore", FakeFeatureStore)
    claim = claim_grouping_work(worker_id="test-f6-worker", limit=1)["claims"][0]
    return report, claim


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
)
def test_feature_claim_to_bounded_grouping_claim_and_defer(
    observe_project, monkeypatch
):
    report, claim = _claimed_runtime(observe_project, monkeypatch)
    occurrence_id = str(report.findings.get().id)
    assert claim["pending_ids"] == [occurrence_id]
    assert claim["candidate_window"] == {
        "registry_revision": 0,
        "issues": [],
        "omitted_candidates": [],
    }
    assert {row["view"] for row in claim["features"]} == {"semantics", "task"}
    assert all(
        row["source_digest"] == canonical_grouping_source_digest(claim["snapshot"])
        for row in claim["features"]
    )
    assert TraceGroupingFeature.no_workspace_objects.count() == 2
    assert set(
        TraceGroupingOutbox.no_workspace_objects.values_list("event_kind", flat=True)
    ) == {
        "error-feed.grouping-feature-ready.v1",
        "error-feed.grouping-ready.v1",
    }

    payload = {
        "attempt_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "idempotency_key": "defer-test",
        "snapshot_digest": claim["snapshot_digest"],
        "registry_revision": claim["registry_revision"],
        "commands": [
            {
                "type": "defer",
                "occurrence_ids": [occurrence_id],
                "reason": "Insufficient mechanism proof",
            }
        ],
        "receipt_ids": [],
    }
    result = publish_grouping(**payload)
    assert result["deferred"] == 1
    with override_settings(ERROR_FEED_GROUPING_ENABLED=False):
        assert publish_grouping(**payload) == result
    report.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED
    assert report.findings.get().cluster_id is None
    assert TraceGroupingDecision.no_workspace_objects.count() == 1
    assert (
        TraceGroupingWork.no_workspace_objects.get(report=report).state == "completed"
    )
    assert (
        canonical_grouping_source_digest(export_grouping_snapshot(report=report))
        == claim["features"][0]["source_digest"]
    )


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
)
def test_raw_receipt_group_binds_create_and_junction(observe_project, monkeypatch):
    report, claim = _claimed_runtime(observe_project, monkeypatch)
    finding = report.findings.get()
    occurrence_id = str(finding.id)
    text = finding.statement
    citation = {
        "occurrence_id": occurrence_id,
        "evidence_id": "report",
        "evidence_digest": _text_digest(text),
        "quote": text[: min(len(text), 24)],
    }
    assert len(citation["quote"]) >= 8
    mechanism = {
        "mechanism": "Incorrect repeated operation",
        "fix_hypothesis": "Use the intended operation once",
        "falsifier": "A verified trace shows only one operation",
    }
    raw_group = {
        "target_issue_id": None,
        "member_ids": [occurrence_id],
        **mechanism,
        "predicted_observations": ["Repeated operation appears"],
        "citations": [
            {
                "finding_id": occurrence_id,
                "evidence_id": "report",
                "evidence_digest": citation["evidence_digest"],
                "quote": citation["quote"],
            }
        ],
        "contradictions": [],
        "alternatives": ["Transport retry"],
        "missing_evidence": [],
    }
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    attempt_id = uuid.UUID(claim["attempt_id"])
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=attempt_id)
    omitted = [{"issue_id": str(uuid.uuid4()), "reason": "ranked_issue_window_bound"}]
    attempt.omitted_candidate_ids = [omitted[0]["issue_id"]]
    attempt.omitted_candidates = omitted
    candidate_window = {**claim["candidate_window"], "omitted_candidates": omitted}
    attempt.candidate_digest = canonical_snapshot_digest(
        {
            "candidate_window": candidate_window,
            "report_digests": {
                str(report.id): canonical_grouping_source_digest(claim["snapshot"])
            },
        }
    )
    attempt.save(
        update_fields=[
            "omitted_candidate_ids",
            "omitted_candidates",
            "candidate_digest",
            "updated_at",
        ]
    )
    receipt = TraceGroupingCall.no_workspace_objects.create(
        scope=scope,
        work=attempt.work,
        attempt=attempt,
        request_key="test-model-request",
        request_digest="sha256:" + "a" * 64,
        status="settled",
        max_cost_usd=Decimal("0.01"),
        cost_usd=Decimal("0.001"),
        result={"groups": [raw_group], "deferred": []},
        model_used="test-model",
    )
    command = {
        "type": "create",
        "temporary_id": "new-f6-issue",
        "occurrence_ids": [occurrence_id],
        "prototype_occurrence_ids": [occurrence_id],
        "mechanism": mechanism,
        "citations": [citation],
        "admission": {
            "primary_receipt_id": str(receipt.id),
            "group_index": 0,
            "repair_receipt_id": None,
        },
    }
    payload = {
        "attempt_id": attempt_id,
        "lease_token": claim["lease_token"],
        "idempotency_key": "create-test",
        "snapshot_digest": claim["snapshot_digest"],
        "registry_revision": claim["registry_revision"],
        "commands": [command],
        "receipt_ids": [str(receipt.id)],
    }
    with pytest.raises(GroupingConflict, match="not offered"):
        publish_grouping(
            **{
                **payload,
                "idempotency_key": "omitted-target-test",
                "commands": [
                    {
                        "type": "attach",
                        "issue_id": omitted[0]["issue_id"],
                        "expected_issue_revision": 1,
                        "occurrence_ids": [occurrence_id],
                        "citations": [citation],
                        "admission": command["admission"],
                    }
                ],
            }
        )
    # The HTTP serializer supplies UUID objects; a string-form replay must
    # produce the same idempotency digest and must not duplicate membership.
    result = publish_grouping(**{**payload, "receipt_ids": [receipt.id]})
    assert publish_grouping(**payload) == result
    assert result["assigned"] == 1
    finding.refresh_from_db()
    assert finding.cluster_id == uuid.UUID(result["created_issue_ids"]["new-f6-issue"])
    assert finding.cluster.cluster_id.startswith("S-")
    membership = ErrorClusterTraces.no_workspace_objects.get(
        finding=finding, deleted=False
    )
    assert membership.cluster_id == finding.cluster_id
    assert membership.trace_id == report.trace_id
    assert membership.span_id is None
    assert membership.trace_session_id is None
    assert finding.cluster.error_count == 1
    assert finding.cluster.severity_assessment_status == "pending"
    assert (
        TraceGroupingSeverityJob.no_workspace_objects.filter(
            issue__cluster=finding.cluster, state="pending"
        ).count()
        == 1
    )


def test_source_digest_excludes_only_mutable_grouping_status(observe_project):
    report = _saved_report(observe_project)
    before = export_grouping_snapshot(report=report)
    report.grouping_status = TraceInvestigationGroupingStatus.COMPLETED
    report.save(update_fields=["grouping_status", "updated_at"])
    after = export_grouping_snapshot(report=report)
    assert before["snapshot_digest"] != after["snapshot_digest"]
    assert canonical_grouping_source_digest(before) == canonical_grouping_source_digest(
        after
    )


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0.025",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="0.025",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="0.025",
)
def test_call_reservation_never_resends_unknown_and_accounts_actual_overage(
    observe_project, monkeypatch
):
    _, claim = _claimed_runtime(observe_project, monkeypatch)
    attempt_id = uuid.UUID(claim["attempt_id"])
    digest = "sha256:" + "a" * 64
    request = {
        "attempt_id": attempt_id,
        "lease_token": claim["lease_token"],
        "request_key": "test-request",
        "request_digest": digest,
    }
    first = reserve_call(**request, max_cost_usd="0.010000000")
    assert first["created"] is True
    assert reserve_call(**request, max_cost_usd="0.010000000")["created"] is False
    # A new estimator must reuse, not overwrite/re-send, an old reservation.
    assert reserve_call(**request, max_cost_usd="0.020000000")["created"] is False
    settled_unknown = settle_call(
        **request,
        status="unknown",
        result={"groups": [], "deferred": []},
        model_used=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )
    assert settled_unknown["result"] == {"groups": [], "deferred": []}
    with pytest.raises(GroupingConflict, match="cannot be overwritten"):
        settle_call(**request, status="unknown", result=None, cost_usd=None)
    known = settle_call(
        **request,
        status="settled",
        result={"groups": [], "deferred": []},
        model_used=None,
        input_tokens=100,
        output_tokens=10,
        cost_usd="0.020000000",
    )
    assert known["status"] == "settled"
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    assert scope.spent_usd == Decimal("0.020000000")
    assert scope.reserved_usd == Decimal("0")
    with pytest.raises(GroupingConflict, match="budget"):
        reserve_call(
            **{**request, "request_key": "next-request"}, max_cost_usd="0.010000000"
        )
    with override_settings(ERROR_FEED_GROUPING_BUDGET_ENFORCED=False):
        uncapped = reserve_call(
            **{**request, "request_key": "next-request"}, max_cost_usd="1.000000000"
        )
        assert uncapped["created"] is True
        scope.refresh_from_db()
        assert scope.reserved_usd == Decimal("1.000000000")
        assert scope.spent_usd == Decimal("0.020000000")


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0.025",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="0.025",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="0.025",
)
def test_kill_switch_blocks_new_work_but_preserves_original_settlement(
    observe_project, monkeypatch
):
    report, claim = _claimed_runtime(observe_project, monkeypatch)
    request = {
        "attempt_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "request_key": "before-disable",
        "request_digest": "sha256:" + "b" * 64,
    }
    assert reserve_call(**request, max_cost_usd="0.010000000")["created"]
    with override_settings(ERROR_FEED_GROUPING_ENABLED=False):
        with pytest.raises(GroupingConflict, match="disabled"):
            reserve_call(
                **{**request, "request_key": "after-disable"},
                max_cost_usd="0.010000000",
            )
        with pytest.raises(GroupingConflict, match="disabled"):
            update_grouping_attempt(
                attempt_id=request["attempt_id"],
                lease_token=request["lease_token"],
                action="renew",
            )
        with pytest.raises(GroupingConflict, match="disabled"):
            checkpoint_attempt(
                attempt_id=request["attempt_id"],
                lease_token=request["lease_token"],
                expected_revision=0,
                checkpoint={},
            )
        with pytest.raises(GroupingConflict, match="disabled"):
            publish_grouping(
                attempt_id=request["attempt_id"],
                lease_token=request["lease_token"],
                idempotency_key="after-disable",
                snapshot_digest=claim["snapshot_digest"],
                registry_revision=claim["registry_revision"],
                commands=[
                    {
                        "type": "defer",
                        "occurrence_ids": [str(report.findings.get().id)],
                        "reason": "disabled",
                    }
                ],
                receipt_ids=[],
            )
        settled = settle_call(
            **request,
            status="unknown",
            result={"groups": [], "deferred": []},
            cost_usd=None,
        )
        assert settled["status"] == "unknown"


def test_stored_contradicted_group_is_not_an_admission():
    receipt = TraceGroupingCall(
        id=uuid.uuid4(),
        result={
            "groups": [
                {
                    "target_issue_id": None,
                    "member_ids": ["member"],
                    "mechanism": "Some mechanism",
                    "fix_hypothesis": "Some fix",
                    "falsifier": "Some falsifier",
                    "predicted_observations": ["prediction"],
                    "citations": [],
                    "contradictions": [{"finding_id": "member"}],
                    "alternatives": ["alternative"],
                    "missing_evidence": [],
                }
            ],
            "deferred": [],
        },
    )
    with pytest.raises(GroupingConflict, match="Emerging"):
        _admitted_group(
            admission={
                "primary_receipt_id": str(receipt.id),
                "group_index": 0,
                "repair_receipt_id": None,
            },
            receipt_map={receipt.id: receipt},
            member_ids=["member"],
            target_issue_id=None,
            mechanism={
                "mechanism": "Some mechanism",
                "fix_hypothesis": "Some fix",
                "falsifier": "Some falsifier",
            },
            expected_action=None,
            required_own_ids={"member"},
        )


def test_source_replacement_recounts_large_protected_issue_without_review_cap(
    observe_project,
):
    report = _saved_report(observe_project)
    scope = TraceGroupingScope.no_workspace_objects.create(
        organization_id=report.organization_id,
        workspace_id=report.workspace_id,
        project_id=report.project_id,
    )
    first = report.findings.get()
    state = _new_issue(
        scope,
        {
            "mechanism": "Repeated operation",
            "fix_hypothesis": "Use once",
            "falsifier": "Only one operation",
        },
        [str(first.id)],
    )
    state.protected = True
    state.save(update_fields=["protected", "updated_at"])
    findings = [first]
    for number in range(1, 17):
        findings.append(
            TraceInvestigationFinding.no_workspace_objects.create(
                id=uuid.uuid4(),
                report=report,
                finding_id=f"finding-{number + 1}",
                ordinal=number,
                kind="outcome",
                statement=f"Repeated operation {number}",
                recovery="not_observed",
            )
        )
    for finding in findings:
        finding.cluster = state.cluster
        finding.save(update_fields=["cluster", "updated_at"])
        ErrorClusterTraces.no_workspace_objects.create(
            cluster=state.cluster,
            finding=finding,
            trace_id=report.trace_id,
        )
    report.is_current = False
    report.save(update_fields=["is_current", "updated_at"])
    successor_attempt = copy.copy(report.attempt)
    successor_attempt.id = uuid.uuid4()
    successor_attempt.generation = 8
    successor_attempt._state.adding = True
    successor_attempt.save(force_insert=True)
    successor = copy.copy(report)
    successor.id = uuid.uuid4()
    successor.attempt = successor_attempt
    successor.idempotency_key = "synthetic-publication-8"
    successor.is_current = True
    successor._state.adding = True
    successor.save(force_insert=True)
    report.job.current_report = successor
    report.job.save(update_fields=["current_report", "updated_at"])

    result = deproject_superseded_report(
        old_report_id=report.id,
        successor_report_id=successor.id,
    )
    assert result["removed"] == 17
    state.refresh_from_db()
    state.cluster.refresh_from_db()
    assert state.protected and not state.retired and not state.dirty
    assert state.cluster.error_count == 0
    assert not ErrorClusterTraces.no_workspace_objects.filter(
        cluster=state.cluster
    ).exists()
