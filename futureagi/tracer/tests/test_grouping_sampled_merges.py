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
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
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
from tracer.queries.grouping_redirect import resolve_issue_redirect
from tracer.services.grouping import context
from tracer.services.grouping import publish as publisher
from tracer.services.grouping.accounting import reserve_call, settle_call
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    GroupingNotFound,
    claim_grouping_work,
)
from tracer.services.grouping.publish import (
    _assign,
    _command_shape,
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


def test_merge_command_shape_accepts_old_and_new_workers():
    fields = publisher.COMMAND_FIELDS["merge"]
    for sampled in (False, True):
        current = {key: ("merge" if key == "type" else None) for key in fields}
        if sampled:
            current["reviewed_occurrence_ids"] = None
        legacy = {**current, "temporary_id": "new-group"}
        del legacy["survivor_issue_id"]
        assert _command_shape(current, sampled=sampled) == current
        assert _command_shape(legacy, sampled=sampled) == legacy
        with pytest.raises(GroupingControlError, match="unknown or missing fields"):
            _command_shape({**current, "temporary_id": "ambiguous"}, sampled=sampled)


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
    survivor = max(sources, key=lambda state: len(_issue_members(state)))
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
                "survivor_issue_id": str(survivor.cluster_id),
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
    sources[0].cluster.rca_synthesis = "Earlier investigation"
    sources[0].cluster.rca_fix = "Earlier fix"
    sources[0].cluster.save(update_fields=["rca_synthesis", "rca_fix", "updated_at"])
    payload = _merge_payload(scope, sources, claim, mechanism)
    result = publish_grouping(**payload)
    target = uuid.UUID(payload["commands"][0]["survivor_issue_id"])
    assert result["created_issue_ids"] == {}
    assert (
        TraceInvestigationFinding.no_workspace_objects.filter(cluster_id=target).count()
        == 35
    )
    assert (
        ErrorClusterTraces.no_workspace_objects.filter(cluster_id=target).count() == 35
    )
    assert (
        TraceGroupingIssueState.all_objects.filter(
            cluster_id=sources[0].cluster_id,
            retired=True,
            deleted=True,
        ).count()
        == 1
    )
    assert not ErrorClusterTraces.no_workspace_objects.filter(
        cluster_id=sources[0].cluster_id
    ).exists()
    assert TraceGroupingIssueState.no_workspace_objects.filter(
        cluster_id=target
    ).exists()
    old_group = TraceErrorGroup.all_objects.get(pk=sources[0].cluster_id)
    assert old_group.deleted is True
    assert old_group.redirect_to_id == target
    assert old_group.rca_synthesis == "Earlier investigation"
    assert old_group.rca_fix == "Earlier fix"
    redirect = resolve_issue_redirect(
        str(sources[0].cluster.cluster_id), [str(scope.project_id)]
    )
    assert (
        redirect["resolved_cluster_id"]
        == TraceGroupingIssueState.no_workspace_objects.get(
            cluster_id=target
        ).cluster.cluster_id
    )
    assert (
        resolve_issue_redirect(str(sources[0].cluster.cluster_id), [str(uuid.uuid4())])
        is None
    )
    assert publish_grouping(**payload) == result
    assert TraceGroupingDecision.no_workspace_objects.filter(scope=scope).count() == 1


@pytest.mark.django_db
def test_legacy_merge_creates_target_and_redirects_both_sources(sampled_claim):
    scope, sources, reports, claim, mechanism = sampled_claim
    payload = _merge_payload(scope, sources, claim, mechanism)
    command = payload["commands"][0]
    command["temporary_id"] = "legacy-merge-target"
    del command["survivor_issue_id"]

    result = publish_grouping(**payload)
    target = uuid.UUID(result["created_issue_ids"]["legacy-merge-target"])
    assert target not in {item.cluster_id for item in sources}
    assert TraceInvestigationFinding.no_workspace_objects.filter(
        cluster_id=target
    ).count() == len(reports)
    for source in sources:
        group = TraceErrorGroup.all_objects.get(pk=source.cluster_id)
        assert group.deleted is True
        assert group.redirect_to_id == target
        assert (
            TraceGroupingIssueState.all_objects.get(
                cluster_id=source.cluster_id
            ).retired
            is True
        )
        assert (
            resolve_issue_redirect(group.cluster_id, [str(scope.project_id)])[
                "resolved_cluster_id"
            ]
            == TraceErrorGroup.all_objects.get(pk=target).cluster_id
        )


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


def test_budget_wait_is_visible_and_requeue_preserves_spend(sampled_claim, caplog):
    from tracer.models.trace_grouping import TraceGroupingFindingState
    from tracer.services.grouping.budget_recovery import requeue_budget_work

    scope, _, _, claim, _ = sampled_claim
    paid = {
        "attempt_id": claim["attempt_id"],
        "lease_token": claim["lease_token"],
        "request_key": "earlier-paid-call",
        "request_digest": "sha256:" + "d" * 64,
    }
    reserve_call(**paid, max_cost_usd="0.01")
    settle_call(
        **paid, status="settled", cost_usd="0.002", failure_code="invalid_output"
    )
    with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0.005"):
        denied = reserve_call(
            attempt_id=claim["attempt_id"],
            lease_token=claim["lease_token"],
            request_key="budget-recovery",
            request_digest="sha256:" + "c" * 64,
            max_cost_usd="0.01",
        )
    assert denied["reason"] == "budget_exhausted:project"
    assert any(
        record.budget_limit == "project"
        for record in caplog.records
        if getattr(record, "event", None) == "grouping_budget_refused"
    )
    reply = publish_grouping(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        idempotency_key="budget-wait",
        snapshot_digest=claim["snapshot_digest"],
        registry_revision=claim["registry_revision"],
        receipt_ids=[],
        commands=[
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": denied["reason"],
            }
        ],
    )
    assert reply["waiting_for_budget"] == len(claim["pending_ids"])
    work = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"]).work
    work.refresh_from_db()
    work.report.refresh_from_db()
    assert work.state == "waiting_budget" and work.report.grouping_status == "pending"
    assert TraceGroupingFindingState.no_workspace_objects.filter(
        finding_id__in=claim["pending_ids"], disposition="waiting_budget"
    ).count() == len(claim["pending_ids"])
    assert claim_grouping_work(worker_id="no-auto-retry", limit=1)["claims"] == []
    with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="0.005"):
        blocked = requeue_budget_work(
            project_id=scope.project_id,
            apply=True,
            expected_registry_revision=reply["registry_revision"],
        )
    assert blocked["works"][0]["blocked_reason"] == "project_budget_unavailable"
    preview = requeue_budget_work(project_id=scope.project_id)
    assert preview["works"][0]["eligible"] and not preview["works"][0]["requeued"]
    with pytest.raises(GroupingConflict):
        requeue_budget_work(
            project_id=scope.project_id, apply=True, expected_registry_revision=-1
        )
    recovered = requeue_budget_work(
        project_id=scope.project_id,
        apply=True,
        expected_registry_revision=reply["registry_revision"],
    )
    assert recovered["works"][0]["requeued"]
    scope.refresh_from_db()
    assert scope.spent_usd == Decimal("0.002") and scope.reserved_usd == 0
    new = claim_grouping_work(worker_id="budget-recovery", limit=1)["claims"][0]
    assert new["pending_ids"] == claim["pending_ids"]
    assert new["checkpoint"] == {} and new["attempt_id"] != claim["attempt_id"]


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_partial_report_recovery_selects_only_unassigned_findings(
    observe_project, monkeypatch
):
    import copy

    from tracer.services.grouping.control import claim_feature_jobs
    from tracer.services.grouping.feature_completion import complete_feature_job
    from tracer.services.grouping_features import enqueue_grouping_features
    from tracer.tests.test_grouping_runtime import _feature_rows, _saved_report

    report = _saved_report(observe_project)
    first = report.findings.get()
    second = copy.copy(first)
    second.pk = uuid.uuid4()
    second.finding_id = "second-finding"
    second.ordinal += 1
    second._state.adding = True
    second.save(force_insert=True)
    from tracer.queries.grouping import export_grouping_snapshot

    export_grouping_snapshot(report=report)
    enqueue_grouping_features(report=report)
    prepared = claim_feature_jobs(worker_id="prepare-recovery", limit=1)["claims"][0]
    complete_feature_job(
        feature_job_id=prepared["feature_job_id"],
        lease_token=prepared["lease_token"],
        status="ready",
        features=_feature_rows(prepared["snapshot"]),
        store=FakeFeatureStore(),
    )
    monkeypatch.setattr(context, "GroupingFeatureStore", FakeFeatureStore)
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    scope.policy_version = SAMPLED_GROUPING_POLICY_VERSION
    scope.save(update_fields=["policy_version", "updated_at"])
    issue = _new_issue(
        scope,
        {
            "mechanism": "Known mechanism",
            "fix_hypothesis": "Fix operation",
            "falsifier": "Operation succeeds",
        },
        [str(first.pk)],
    )
    _assign(first, issue, scope)
    claim = claim_grouping_work(worker_id="partial-recovery", limit=1)["claims"][0]
    assert claim["pending_ids"] == [str(second.pk)]
    assert (
        len(claim["snapshot"]["occurrences"]) == 2
    )  # Full source binding remains intact.
    result = publish_grouping(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        idempotency_key="partial-recovery",
        snapshot_digest=claim["snapshot_digest"],
        registry_revision=claim["registry_revision"],
        receipt_ids=[],
        commands=[
            {
                "type": "defer",
                "occurrence_ids": [str(second.pk)],
                "reason": "budget_exhausted:work",
            }
        ],
    )
    first.refresh_from_db()
    assert first.cluster_id == issue.cluster_id and result["waiting_for_budget"] == 1


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_requeued_peer_keeps_original_cohort_spending_limit(
    observe_project, monkeypatch
):
    from tracer.services.grouping.budget_recovery import requeue_budget_work

    _prepare_runtime(observe_project, monkeypatch, identity="budget-primary")
    _prepare_runtime(observe_project, monkeypatch, identity="budget-peer")
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    scope.policy_version = SAMPLED_GROUPING_POLICY_VERSION
    scope.save(update_fields=["policy_version", "updated_at"])
    claim = claim_grouping_work(worker_id="cohort-spend", limit=1)["claims"][0]
    original = TraceGroupingAttempt.no_workspace_objects.get(
        pk=claim["attempt_id"]
    ).work
    peer = (
        TraceGroupingWork.no_workspace_objects.filter(scope=scope)
        .exclude(pk=original.pk)
        .get()
    )
    assert peer.budget_work_id == original.pk
    call = {
        "attempt_id": claim["attempt_id"],
        "lease_token": claim["lease_token"],
        "request_key": "paid-cohort-call",
        "request_digest": "sha256:" + "f" * 64,
    }
    reserve_call(**call, max_cost_usd="0.01")
    settle_call(
        **call, status="settled", cost_usd="0.002", failure_code="invalid_output"
    )
    reply = publish_grouping(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        idempotency_key="cohort-budget-wait",
        snapshot_digest=claim["snapshot_digest"],
        registry_revision=claim["registry_revision"],
        receipt_ids=[],
        commands=[
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": "budget_exhausted:work",
            }
        ],
    )
    # Simulate the primary report already having a validated placement. Only the
    # other report needs recovery and therefore becomes the next primary work.
    first = original.report.findings.get()
    issue = _new_issue(
        scope,
        {
            "mechanism": "Known failure",
            "fix_hypothesis": "Fix operation",
            "falsifier": "Operation succeeds",
        },
        [str(first.pk)],
    )
    _assign(first, issue, scope)
    original.state = "completed"
    original.save(update_fields=["state", "updated_at"])
    with override_settings(ERROR_FEED_GROUPING_WORK_BUDGET_USD="0.005"):
        blocked = requeue_budget_work(project_id=scope.project_id)
    assert blocked["works"][0]["blocked_reason"] == "work_budget_unavailable"
    recovered = requeue_budget_work(
        project_id=scope.project_id,
        apply=True,
        expected_registry_revision=reply["registry_revision"],
    )
    assert recovered["works"][0]["requeued"]
    new = claim_grouping_work(worker_id="peer-recovery", limit=1)["claims"][0]
    assert (
        TraceGroupingAttempt.no_workspace_objects.get(pk=new["attempt_id"]).work_id
        == peer.pk
    )
    with override_settings(ERROR_FEED_GROUPING_WORK_BUDGET_USD="0.005"):
        denied = reserve_call(
            attempt_id=new["attempt_id"],
            lease_token=new["lease_token"],
            request_key="new-peer-call",
            request_digest="sha256:" + "e" * 64,
            max_cost_usd="0.01",
        )
    assert denied["reason"] == "budget_exhausted:work"
    scope.refresh_from_db()
    assert scope.spent_usd == Decimal("0.002") and scope.reserved_usd == 0


@pytest.mark.parametrize(
    "status,sampled", [("acknowledged", True), ("resolved", True), ("resolved", False)]
)
@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_reviewed_attachment_preserves_identity_and_reopens_resolved_issue(
    observe_project, monkeypatch, status, sampled
):
    old = _prepare_runtime(observe_project, monkeypatch, identity="reviewed-existing")
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    if sampled:
        scope.policy_version = SAMPLED_GROUPING_POLICY_VERSION
        scope.save(update_fields=["policy_version", "updated_at"])
    mechanism = {
        "title": "Refund uses the wrong amount",
        "mechanism": "Incorrect refund amount",
        "fix_hypothesis": "Refund the requested amount",
        "falsifier": "Correct amount refunded",
    }
    first = old.findings.get()
    issue = _new_issue(scope, mechanism, [str(first.id)])
    _assign(first, issue, scope)
    issue.cluster.status = status
    issue.cluster.rca_synthesis = "Human-reviewed root cause"
    issue.cluster.rca_fix = "Human-reviewed fix"
    issue.cluster.save(
        update_fields=["status", "rca_synthesis", "rca_fix", "updated_at"]
    )
    TraceGroupingWork.no_workspace_objects.filter(scope=scope).update(state="completed")
    new = _prepare_runtime(observe_project, monkeypatch, identity="reviewed-recurrence")
    second = new.findings.get()

    class Candidates(FakeFeatureStore):
        def candidate_occurrences(self, **kwargs):
            return [str(first.id)]

    monkeypatch.setattr(context, "GroupingFeatureStore", Candidates)
    claim = claim_grouping_work(worker_id="reviewed-attachment", limit=1)["claims"][0]
    assert claim["candidate_window"]["issues"][0]["protected"]
    citations = [
        {
            "occurrence_id": str(row.id),
            "evidence_id": "report",
            "evidence_digest": _text_digest(row.statement),
            "quote": row.statement[:24],
        }
        for row in [first, second]
    ]
    raw = {
        "target_issue_id": str(issue.cluster_id),
        "member_ids": [str(second.id)],
        **mechanism,
        "predicted_observations": ["Requested and executed amounts differ"],
        "citations": [
            {
                "finding_id": item["occurrence_id"],
                **{key: value for key, value in item.items() if key != "occurrence_id"},
            }
            for item in citations
        ],
        "contradictions": [],
        "alternatives": ["Recognition failure"],
        "missing_evidence": [],
    }
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    receipt = TraceGroupingCall.no_workspace_objects.create(
        scope=scope,
        work=attempt.work,
        attempt=attempt,
        request_key="reviewed-attach",
        request_digest="sha256:" + "a" * 64,
        status="settled",
        max_cost_usd=Decimal("0.01"),
        cost_usd=Decimal("0.001"),
        result={"groups": [raw], "deferred": []},
    )
    payload = {
        "attempt_id": attempt.pk,
        "lease_token": claim["lease_token"],
        "idempotency_key": "reviewed-attach",
        "snapshot_digest": claim["snapshot_digest"],
        "registry_revision": claim["registry_revision"],
        "receipt_ids": [str(receipt.pk)],
        "commands": [
            {
                "type": "attach",
                "issue_id": str(issue.cluster_id),
                "expected_issue_revision": issue.revision,
                "occurrence_ids": [str(second.pk)],
                "citations": citations,
                "admission": {
                    "primary_receipt_id": str(receipt.pk),
                    "group_index": 0,
                    "repair_receipt_id": None,
                },
            }
        ],
    }
    if not sampled:
        with pytest.raises(GroupingConflict, match="attach target"):
            publish_grouping(**payload)
        second.refresh_from_db()
        issue.cluster.refresh_from_db()
        assert second.cluster_id is None and issue.cluster.status == "resolved"
        return
    if status == "resolved":
        import copy

        invalid = copy.deepcopy(payload)
        invalid["idempotency_key"] = "rollback-reviewed-attach"
        invalid["commands"].append(
            {
                "type": "defer",
                "occurrence_ids": [str(second.pk)],
                "reason": "duplicate disposition",
            }
        )
        with pytest.raises(GroupingConflict, match="duplicated or not pending"):
            publish_grouping(**invalid)
        issue.refresh_from_db()
        issue.cluster.refresh_from_db()
        second.refresh_from_db()
        assert second.cluster_id is None and issue.cluster.status == "resolved"
        assert not issue.protected
        assert not TraceGroupingDecision.no_workspace_objects.filter(
            idempotency_key="rollback-reviewed-attach"
        ).exists()
    result = publish_grouping(**payload)
    issue.refresh_from_db()
    issue.cluster.refresh_from_db()
    second.refresh_from_db()
    assert second.cluster_id == issue.cluster_id and issue.cluster.error_count == 2
    assert issue.mechanism == mechanism and issue.revision == 1
    assert issue.cluster.title == mechanism["title"]
    assert issue.cluster.rca_synthesis == "Human-reviewed root cause"
    assert issue.cluster.rca_fix == "Human-reviewed fix"
    assert issue.cluster.status == ("for_review" if status == "resolved" else status)
    if status == "resolved":
        assert issue.protected
        assert result["reopened_issues"][0]["reason"] == "new_occurrence"
        assert result["reopened_issues"][0]["from_status"] == "resolved"
        assert result["reopened_issues"][0]["occurrence_ids"] == [str(second.pk)]
    else:
        assert result["reopened_issues"] == []
    assert publish_grouping(**payload) == result
    assert (
        TraceGroupingDecision.no_workspace_objects.filter(attempt=attempt).count() == 1
    )
