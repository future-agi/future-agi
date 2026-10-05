"""Sampled review versus complete, atomic membership publication. Not live inference."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from tracer.constants.grouping_versions import SAMPLED_GROUPING_POLICY_VERSION
from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingConstraint,
    TraceGroupingDecision,
    TraceGroupingIssueState,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import TraceInvestigationFinding
from tracer.services.grouping import context
from tracer.services.grouping import publish as publisher
from tracer.services.grouping.accounting import reserve_call, settle_call
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingNotFound,
    claim_grouping_work,
)
from tracer.services.grouping.publish import (
    _assign,
    _issue_members,
    _new_issue,
    _text_digest,
    publish_grouping,
)
from tracer.services.grouping.sampling import (
    membership_binding,
    sample_issue_members,
)
from tracer.tests.test_grouping_runtime import FakeFeatureStore, _prepare_runtime


def test_sample_keeps_prototypes_recent_and_diverse_examples_deterministically():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=str(i),
            statement="agent waits instead of stating call purpose",
            report=SimpleNamespace(recorded_at=start + timedelta(seconds=i)),
        )
        for i in range(30)
    ]
    rows[15].statement = "voicemail playback interrupted before delivery"
    sample = sample_issue_members(rows, [str(i) for i in range(5)])
    assert len(sample) == 8
    assert {str(i) for i in range(5)} | {"15", "29"} <= {item.id for item in sample}
    assert [item.id for item in sample] == [
        item.id
        for item in sample_issue_members(
            list(reversed(rows)), [str(i) for i in range(5)]
        )
    ]


def test_full_membership_binding_changes_when_an_unseen_member_changes():
    state = SimpleNamespace(membership_revision=4)
    assert membership_binding(state, ["a", "b"]) != membership_binding(
        state, ["a", "c"]
    )


@pytest.fixture
def sampled_claim(observe_project, monkeypatch):
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_ALL_PROJECTS=True,
        ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
        ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
        ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
        ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
    ):
        reports = [
            _prepare_runtime(observe_project, monkeypatch, identity=f"large-{i}")
            for i in range(35)
        ]
        scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
        scope.policy_version = SAMPLED_GROUPING_POLICY_VERSION
        scope.save(update_fields=["policy_version", "updated_at"])
        mechanism = {
            "title": "Refund uses the wrong amount",
            "mechanism": "Only 10 was refunded instead of 100",
            "fix_hypothesis": "Use the requested amount for the refund",
            "falsifier": "The captured refund used the correct amount",
        }
        sources = []
        for group in (reports[:17], reports[17:]):
            state = _new_issue(scope, mechanism, [str(group[0].findings.get().id)])
            for report in group:
                _assign(report.findings.get(), state, scope)
            sources.append(state)
        TraceGroupingWork.no_workspace_objects.filter(scope=scope).update(
            state="completed"
        )
        _prepare_runtime(
            observe_project, monkeypatch, identity="pending-for-large-merge"
        )

        class CandidateStore(FakeFeatureStore):
            def candidate_occurrences(self, **kwargs):
                return [
                    key for source in sources for key in source.prototype_occurrence_ids
                ]

        monkeypatch.setattr(context, "GroupingFeatureStore", CandidateStore)
        claim = claim_grouping_work(worker_id="sample-test", limit=1)["claims"][0]
        yield scope, sources, reports, claim, mechanism


def _merge_payload(scope, sources, claim, mechanism):
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    sample_ids = [
        item["occurrence_id"]
        for issue in claim["candidate_window"]["issues"]
        for item in issue["members"]
    ]
    citations = []
    for finding in TraceInvestigationFinding.no_workspace_objects.filter(
        id__in=sample_ids
    ):
        citations.append(
            {
                "occurrence_id": str(finding.id),
                "evidence_id": "report",
                "evidence_digest": _text_digest(finding.statement),
                "quote": finding.statement[:24],
            }
        )
    raw = {
        "target_issue_id": None,
        "member_ids": sample_ids,
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
    reservation = reserve_call(
        attempt_id=attempt.id,
        lease_token=claim["lease_token"],
        request_key="merge-review:test",
        request_digest="sha256:" + "a" * 64,
        max_cost_usd="0.010000000",
    )
    settle_call(
        attempt_id=attempt.id,
        lease_token=claim["lease_token"],
        request_key="merge-review:test",
        request_digest="sha256:" + "a" * 64,
        status="settled",
        cost_usd="0.001000000",
        result={
            "action": "merge",
            "groups": [raw],
            "removed_ids": [],
            "reason": "Same failure",
        },
    )
    return {
        "attempt_id": attempt.id,
        "lease_token": claim["lease_token"],
        "idempotency_key": "sampled-publication",
        "snapshot_digest": claim["snapshot_digest"],
        "registry_revision": claim["registry_revision"],
        "receipt_ids": [reservation["receipt_id"]],
        "commands": [
            {
                "type": "merge",
                "source_issue_ids": [str(item.cluster_id) for item in sources],
                "expected_revisions": {
                    str(item.cluster_id): item.revision for item in sources
                },
                "temporary_id": "merged-large",
                "mechanism": mechanism,
                "prototype_occurrence_ids": [sample_ids[0]],
                "reviewed_occurrence_ids": sample_ids,
                "citations": citations,
                "admission": {
                    "primary_receipt_id": reservation["receipt_id"],
                    "group_index": 0,
                    "repair_receipt_id": None,
                },
            },
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": "uncertain",
            },
        ],
    }


@pytest.mark.django_db
def test_large_issues_are_offered_with_eight_examples_and_merge_all_members(
    sampled_claim,
):
    scope, sources, reports, claim, mechanism = sampled_claim
    issues = claim["candidate_window"]["issues"]
    assert sorted(item["member_count"] for item in issues) == [17, 18]
    assert all(
        len(item["members"]) == 8 and not item["membership_complete"] for item in issues
    )
    assert len(claim["candidate_snapshots"]) == 16
    assert all(len(_issue_members(item)) > 16 for item in sources)
    payload = _merge_payload(scope, sources, claim, mechanism)
    result = publish_grouping(**payload)
    target = uuid.UUID(result["created_issue_ids"]["merged-large"])
    assert (
        TraceInvestigationFinding.no_workspace_objects.filter(cluster_id=target).count()
        == 35
    )
    assert (
        ErrorClusterTraces.no_workspace_objects.filter(cluster_id=target).count() == 35
    )
    assert (
        TraceGroupingIssueState.no_workspace_objects.filter(
            cluster_id__in=[item.cluster_id for item in sources], retired=True
        ).count()
        == 2
    )
    assert not ErrorClusterTraces.no_workspace_objects.filter(
        cluster_id__in=[item.cluster_id for item in sources]
    ).exists()
    assert publish_grouping(**payload) == result
    assert TraceGroupingDecision.no_workspace_objects.filter(scope=scope).count() == 1


@pytest.mark.django_db
def test_failed_optional_review_remains_accounted_without_blocking_valid_publication(
    sampled_claim,
):
    scope, sources, _, claim, mechanism = sampled_claim
    payload = _merge_payload(scope, sources, claim, mechanism)
    common = {
        "attempt_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "request_key": "merge-review:failed",
        "request_digest": "sha256:" + "b" * 64,
    }
    failed = reserve_call(**common, max_cost_usd="0.010000000")
    settle_call(**common, status="settled", cost_usd="0.001000000", result=None)
    assert failed["receipt_id"] not in payload["receipt_ids"]
    assert publish_grouping(**payload)["status"] == "completed"
    ledger = TraceGroupingCall.no_workspace_objects.get(pk=failed["receipt_id"])
    assert ledger.result is None and ledger.cost_usd == Decimal("0.001000000")
    assert (
        TraceGroupingDecision.no_workspace_objects.get(scope=scope).receipt_ids
        == payload["receipt_ids"]
    )


def _finding_lock_capture(locked_parameters):
    def capture(execute, sql, params, many, context):
        if '"tracer_trace_investigation_finding"' in sql and "FOR UPDATE" in sql:
            locked_parameters.append(" ".join(str(value) for value in (params or ())))
        return execute(sql, params, many, context)

    return capture


@pytest.mark.django_db
def test_deferral_does_not_lock_unseen_members_of_unchanged_candidates(sampled_claim):
    _, sources, reports, claim, _ = sampled_claim
    sampled = {
        item["occurrence_id"]
        for issue in claim["candidate_window"]["issues"]
        for item in issue["members"]
    }
    unseen = {str(report.findings.get().id) for report in reports} - sampled
    locked = []
    with connection.execute_wrapper(_finding_lock_capture(locked)):
        result = publish_grouping(
            attempt_id=uuid.UUID(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            idempotency_key="defer-with-large-candidates",
            snapshot_digest=claim["snapshot_digest"],
            registry_revision=claim["registry_revision"],
            receipt_ids=[],
            commands=[
                {
                    "type": "defer",
                    "occurrence_ids": claim["pending_ids"],
                    "reason": "uncertain",
                }
            ],
        )
    parameters = " ".join(locked)
    assert locked and unseen
    assert not any(key in parameters for key in unseen)
    assert result["status"] == "completed"
    assert sorted(len(_issue_members(item)) for item in sources) == [17, 18]


@pytest.mark.django_db
def test_actual_merge_locks_every_source_finding(sampled_claim):
    scope, sources, reports, claim, mechanism = sampled_claim
    payload = _merge_payload(scope, sources, claim, mechanism)
    full_ids = {str(report.findings.get().id) for report in reports}
    locked = []
    with connection.execute_wrapper(_finding_lock_capture(locked)):
        assert publish_grouping(**payload)["status"] == "completed"
    assert all(key in " ".join(locked) for key in full_ids)


@pytest.mark.django_db
def test_full_evidence_window_skips_later_candidate_membership_scans(
    sampled_claim, monkeypatch
):
    _, _, _, claim, _ = sampled_claim
    inspected = []
    original = publisher._issue_evidence

    def evidence(state, **kwargs):
        inspected.append(str(state.cluster_id))
        return original(state, **kwargs)

    monkeypatch.setattr(context, "MAX_CANDIDATE_MEMBERS", 8)
    monkeypatch.setattr(publisher, "_issue_evidence", evidence)
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    response = context.build_claim_context(
        attempt=attempt, pending_snapshots=claim["pending_snapshots"]
    )
    assert len(inspected) == 1
    assert len(response["candidate_window"]["issues"]) == 1
    assert any(
        item["reason"] == "full_membership_bound"
        for item in response["candidate_window"]["omitted_candidates"]
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    ["protected", "unseen_cannot_link", "incomplete_sample", "membership_change"],
)
def test_invalid_sampled_merge_rolls_back_every_membership_change(
    sampled_claim, failure, monkeypatch
):
    scope, sources, reports, claim, mechanism = sampled_claim
    payload = _merge_payload(scope, sources, claim, mechanism)
    if failure == "protected":
        TraceGroupingIssueState.no_workspace_objects.filter(pk=sources[0].pk).update(
            protected=True
        )
    elif failure == "unseen_cannot_link":
        reviewed = set(payload["commands"][0]["reviewed_occurrence_ids"])
        unseen = next(
            item.findings.get()
            for item in reports[:17]
            if str(item.findings.get().id) not in reviewed
        )
        peer = reports[17].findings.get()
        TraceGroupingConstraint.no_workspace_objects.create(
            scope=scope, kind="cannot_link", first_finding=unseen, second_finding=peer
        )
    elif failure == "membership_change":
        replacement = _prepare_runtime(
            scope.project, monkeypatch, identity="changed-member"
        )
        _assign(replacement.findings.get(), sources[0], scope)
    else:
        payload["commands"][0]["reviewed_occurrence_ids"].pop()
    expected_counts = sorted(len(_issue_members(item)) for item in sources)
    with pytest.raises(GroupingConflict):
        publish_grouping(**payload)
    assert not TraceGroupingDecision.no_workspace_objects.filter(scope=scope).exists()
    assert sorted(len(_issue_members(item)) for item in sources) == expected_counts
    assert not TraceGroupingIssueState.no_workspace_objects.filter(
        scope=scope, retired=True
    ).exists()


@pytest.mark.django_db
@override_settings(ERROR_FEED_GROUPING_BUDGET_ENFORCED=False)
@pytest.mark.parametrize("count,budget", [(2, "0.02"), (10, "1")])
def test_merge_budget_is_durable_and_independent_of_general_budget_switch(
    sampled_claim, count, budget
):
    _, _, _, claim, _ = sampled_claim
    common = {
        "attempt_id": uuid.UUID(claim["attempt_id"]),
        "lease_token": claim["lease_token"],
        "max_cost_usd": "0.010000000",
    }
    with override_settings(ERROR_FEED_GROUPING_MERGE_BUDGET_USD=budget):
        for i in range(count):
            result = reserve_call(
                **common,
                request_key=f"merge-review:{i}",
                request_digest="sha256:" + str(i) * 64,
            )
            assert result["created"]
        denied = reserve_call(
            **common,
            request_key="merge-review:third",
            request_digest="sha256:" + "f" * 64,
        )
        assert denied["status"] == "budget_exhausted"
        cached = reserve_call(
            **common, request_key="merge-review:0", request_digest="sha256:" + "0" * 64
        )
        assert not cached["created"]
        assert (
            TraceGroupingCall.no_workspace_objects.filter(
                request_key__startswith="merge-review:"
            ).count()
            == count
        )


@pytest.mark.django_db
def test_sampled_general_budget_denial_has_no_receipt_and_preserves_cached_calls(
    sampled_claim,
):
    _, _, _, claim, _ = sampled_claim
    common = {
        "attempt_id": claim["attempt_id"],
        "lease_token": claim["lease_token"],
        "max_cost_usd": "0.01",
    }
    existing = reserve_call(
        **common, request_key="discovery:cached", request_digest="sha256:" + "a" * 64
    )
    with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0.005"):
        denied = reserve_call(
            **common, request_key="discovery:new", request_digest="sha256:" + "b" * 64
        )
        assert denied["status"] == "budget_exhausted"
        assert denied["created"] is False and "receipt_id" not in denied
        cached = reserve_call(
            **common,
            request_key="discovery:cached",
            request_digest="sha256:" + "a" * 64,
        )
        assert (
            cached["receipt_id"] == existing["receipt_id"]
            and cached["created"] is False
        )
        with pytest.raises(GroupingNotFound):
            reserve_call(
                **{**common, "lease_token": "stale-token"},
                request_key="discovery:new",
                request_digest="sha256:" + "b" * 64,
            )
    assert TraceGroupingCall.no_workspace_objects.count() == 1


@pytest.mark.django_db
@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_activation_is_explicit_and_does_not_requeue_or_reconcile(
    observe_project, monkeypatch
):
    _prepare_runtime(observe_project, monkeypatch, identity="activation-preview")
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    before = list(
        TraceGroupingWork.no_workspace_objects.filter(scope=scope).values(
            "id", "state", "attempt_number"
        )
    )
    call_command(
        "enable_sampled_grouping",
        project_id=observe_project.id,
        apply=False,
        expected_registry_revision=None,
        stdout=StringIO(),
    )
    scope.refresh_from_db()
    assert scope.policy_version == "f6-minilm/v1"
    revision = scope.registry_revision
    call_command(
        "enable_sampled_grouping",
        project_id=observe_project.id,
        apply=True,
        expected_registry_revision=revision,
        stdout=StringIO(),
    )
    scope.refresh_from_db()
    assert scope.policy_version == SAMPLED_GROUPING_POLICY_VERSION
    assert scope.registry_revision == revision + 1
    assert (
        list(
            TraceGroupingWork.no_workspace_objects.filter(scope=scope).values(
                "id", "state", "attempt_number"
            )
        )
        == before
    )
    assert not TraceGroupingDecision.no_workspace_objects.filter(scope=scope).exists()
