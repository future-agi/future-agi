"""Cross-batch deferrals get a fresh, budget-preserving comparison opportunity."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Event
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone

from simulate.models import Scenarios
from simulate.models.test_execution import CallExecution
from tracer.constants.grouping_versions import (
    GROUPING_POLICY_VERSION,
    SIMULATION_FEATURE_POLICY_VERSION,
    SIMULATION_GROUPING_POLICY_VERSION,
)
from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingCall,
    TraceGroupingFeature,
    TraceGroupingFeatureJob,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.models.trace_investigation import TraceInvestigationReport
from tracer.services.grouping import context
from tracer.services.grouping.control import claim_feature_jobs, claim_grouping_work
from tracer.services.grouping.publish import _assign, _new_issue, publish_grouping
from tracer.services.grouping.revisit import requeue_deferred_reports
from tracer.services.trace_investigation import (
    canonical_wire_result_digest,
    claim_due_investigations,
    publish_investigation,
)
from tracer.tests.test_grouping_runtime import FakeFeatureStore
from tracer.tests.test_simulation_investigation import (
    _execution,
    _failure_result,
    _publish,
    _ready_features,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def simulation_reports(auth_client, organization, workspace, monkeypatch):
    monkeypatch.setattr(context, "GroupingFeatureStore", FakeFeatureStore)
    with override_settings(
        ERROR_FEED_GROUPING_ENABLED=True,
        ERROR_FEED_GROUPING_BUDGET_ENFORCED=False,
        ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ):

        def prepare(count, *, policy=None, extra_behavior=False):
            scenario = Scenarios.objects.create(
                name="Refund",
                source="Refund policy",
                organization=organization,
                workspace=workspace,
            )
            execution, _ = _execution(
                organization,
                workspace,
                scenario,
                "batch",
                CallExecution.CallStatus.COMPLETED,
            )
            for _ in range(count - 1):
                CallExecution.objects.create(
                    test_execution=execution,
                    scenario=scenario,
                    status=CallExecution.CallStatus.COMPLETED,
                )
            assert (
                auth_client.post(
                    f"/simulate/test-executions/{execution.id}/debug-analysis/"
                ).status_code
                == 202
            )
            while claims := claim_due_investigations(
                worker_id="test", engine_version="omega-v1", limit=10
            )["claims"]:
                for claim in claims:
                    if policy:
                        TraceGroupingScope.no_workspace_objects.get_or_create(
                            project_id=claim["project_id"],
                            defaults={
                                "organization": organization,
                                "workspace": workspace,
                                "policy_version": policy,
                            },
                        )
                    if extra_behavior:
                        from tracer.models.trace_investigation import (
                            TraceInvestigationJob,
                        )

                        call = TraceInvestigationJob.no_workspace_objects.get(
                            id=claim["job_id"]
                        ).call_execution
                        result = _failure_result(claim, call)
                        extra = deepcopy(result["findings"][0])
                        extra.update(
                            finding_id="unanswered-question",
                            requirement_id=None,
                            statement="The caller asked for a receipt but the agent ignored that question.",
                        )
                        result["findings"].append(extra)
                        result["result_digest"] = canonical_wire_result_digest(result)
                        publish_investigation(
                            idempotency_key=str(claim["attempt_id"]),
                            lease_token=claim["lease_token"],
                            result=result,
                            wire_result_digest=result["result_digest"],
                        )
                    else:
                        _publish(claim)
                _ready_features()
            return list(
                TraceInvestigationReport.no_workspace_objects.filter(
                    test_execution=execution
                ).order_by("id")
            )

        yield prepare


def _defer(claim):
    return publish_grouping(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        idempotency_key=claim["attempt_id"],
        snapshot_digest=claim["snapshot_digest"],
        registry_revision=claim["registry_revision"],
        receipt_ids=[],
        commands=[
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": "Ambiguous correction",
            }
        ],
    )


def test_simulation_claim_compares_more_than_twenty_reports(simulation_reports):
    reports = simulation_reports(24)
    (claim,) = claim_grouping_work(worker_id="test", limit=1)["claims"]
    assert len(claim["pending_snapshots"]) == len(reports) == 24
    assert len(claim["pending_ids"]) == 24
    assert claim["policy_version"] == "f6-minilm-simulation/v3"


def test_new_target_requeues_earlier_deferrals_but_no_progress_pass_stops(
    simulation_reports,
):
    reports = simulation_reports(2)
    first, second = [
        TraceGroupingWork.no_workspace_objects.get(report=report) for report in reports
    ]
    # A valid backoff separates two report batches without altering claim bounds.
    second.not_before = timezone.now() + timedelta(hours=1)
    second.failure_code = "gateway_error"
    second.save(update_fields=["not_before", "failure_code"])
    (original,) = claim_grouping_work(worker_id="test", limit=1)["claims"]
    assert len(original["pending_ids"]) == 1
    _defer(original)
    first.refresh_from_db()
    original_budget = first.budget_work_id
    first_attempt_number = first.attempt_number
    assert first.state == "completed"
    second.not_before = timezone.now()
    second.failure_code = ""
    second.save(update_fields=["not_before", "failure_code"])
    (later,) = claim_grouping_work(worker_id="test", limit=1)["claims"]
    _defer(later)
    source = TraceGroupingAttempt.no_workspace_objects.get(id=later["attempt_id"])
    # Exercise the registry-change transition under the same lock as publication.
    with transaction.atomic():
        scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
            id=first.scope_id
        )
        assert (
            requeue_deferred_reports(
                scope=scope, source_attempt=source, reviewed_work_ids=[second.id]
            )
            == 1
        )
        assert (
            requeue_deferred_reports(
                scope=scope, source_attempt=source, reviewed_work_ids=[second.id]
            )
            == 0
        )
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.state, second.state) == ("pending", "completed")
    assert first.budget_work_id == original_budget
    assert first.attempt_number == first.retry_start_attempt == first_attempt_number
    (revisit,) = claim_grouping_work(worker_id="test", limit=1)["claims"]
    assert revisit["pending_ids"] == original["pending_ids"]
    assert revisit["checkpoint"] == {}
    assert revisit["attempt_id"] != original["attempt_id"]
    _defer(revisit)
    assert claim_grouping_work(worker_id="test", limit=1)["claims"] == []
    first.refresh_from_db()
    assert first.state == "completed"


def test_explicit_upgrade_preserves_history_and_rebuilds_broader_features(
    simulation_reports,
):
    # Reproduce a report published before the immutable admission gate existed.
    with patch("tracer.services.trace_investigation.GROUPING_GATE_VERSION", None):
        (report,) = simulation_reports(
            1, policy=GROUPING_POLICY_VERSION, extra_behavior=True
        )
    (claim,) = claim_grouping_work(worker_id="legacy", limit=1)["claims"]
    assert len(claim["pending_ids"]) == 1
    _defer(claim)
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    old_job_id = work.feature_job_id
    old_features = set(
        TraceGroupingFeature.no_workspace_objects.values_list("id", flat=True)
    )
    scope = TraceGroupingScope.no_workspace_objects.get(id=work.scope_id)
    scope.spent_usd = Decimal("1.25")
    scope.save(update_fields=["spent_usd"])
    kwargs = {"project_id": scope.project_id, "stdout": StringIO()}
    call_command("enable_simulation_grouping", **kwargs)
    scope.refresh_from_db()
    assert scope.policy_version == GROUPING_POLICY_VERSION
    assert TraceGroupingFeatureJob.no_workspace_objects.count() == 1
    with pytest.raises(CommandError, match="revision changed"):
        call_command(
            "enable_simulation_grouping",
            apply=True,
            expected_registry_revision=-1,
            **kwargs,
        )
    call_command(
        "enable_simulation_grouping",
        apply=True,
        expected_registry_revision=scope.registry_revision,
        **kwargs,
    )
    scope.refresh_from_db()
    work.refresh_from_db()
    assert scope.policy_version == SIMULATION_GROUPING_POLICY_VERSION
    assert scope.spent_usd == Decimal("1.25")
    assert work.state == "superseded"
    assert (
        TraceGroupingFeatureJob.no_workspace_objects.get(id=old_job_id).state
        == "superseded"
    )
    assert old_features <= set(
        TraceGroupingFeature.no_workspace_objects.values_list("id", flat=True)
    )
    _ready_features()
    work.refresh_from_db()
    assert work.state == "pending"
    assert work.feature_job_id != old_job_id
    assert work.feature_job.policy_version == SIMULATION_FEATURE_POLICY_VERSION
    assert work.feature_job.tracegroupingfeature_set.count() == 4
    assert work.attempt_number == work.retry_start_attempt == 1
    (updated,) = claim_grouping_work(worker_id="behavior", limit=1)["claims"]
    assert len(updated["pending_ids"]) == 2
    assert updated["checkpoint"] == {}
    assert updated["policy_version"] == SIMULATION_GROUPING_POLICY_VERSION


def test_rechecks_do_not_reparent_existing_paid_cohort_budgets(simulation_reports):
    reports = simulation_reports(2)
    works = [
        TraceGroupingWork.no_workspace_objects.get(report=report) for report in reports
    ]
    second = works[1]
    second.not_before = timezone.now() + timedelta(hours=1)
    second.failure_code = "gateway_error"
    second.save(update_fields=["not_before", "failure_code"])
    attempts = []
    for index in range(2):
        if index:
            second.not_before = timezone.now()
            second.failure_code = ""
            second.save(update_fields=["not_before", "failure_code"])
        (claim,) = claim_grouping_work(worker_id="separate-budget", limit=1)["claims"]
        attempt = TraceGroupingAttempt.no_workspace_objects.get(id=claim["attempt_id"])
        TraceGroupingCall.no_workspace_objects.create(
            scope=attempt.work.scope,
            work=attempt.work,
            attempt=attempt,
            request_key=f"synthetic-{index}",
            request_digest="sha256:" + "a" * 64,
            status="settled",
            max_cost_usd=Decimal("0.2"),
            cost_usd=Decimal("0.1"),
        )
        _defer(claim)
        attempts.append(attempt)
    with transaction.atomic():
        scope = TraceGroupingScope.no_workspace_objects.select_for_update().get(
            id=works[0].scope_id
        )
        assert (
            requeue_deferred_reports(
                scope=scope, source_attempt=attempts[-1], reviewed_work_ids=[]
            )
            == 2
        )
    (revisit,) = claim_grouping_work(worker_id="recheck", limit=1)["claims"]
    assert len(revisit["pending_ids"]) == 1
    for work in works:
        work.refresh_from_db()
        assert work.budget_work_id is None
    assert {work.state for work in works} == {"running", "pending"}


def test_upgrade_rejects_live_attempt_and_keeps_fully_assigned_reports_complete(
    simulation_reports,
):
    (report,) = simulation_reports(1, policy=GROUPING_POLICY_VERSION)
    (claim,) = claim_grouping_work(worker_id="legacy", limit=1)["claims"]
    scope = TraceGroupingScope.no_workspace_objects.get(project=report.project)
    kwargs = {
        "project_id": scope.project_id,
        "apply": True,
        "expected_registry_revision": scope.registry_revision,
        "stdout": StringIO(),
    }
    with pytest.raises(CommandError, match="Drain active"):
        call_command("enable_simulation_grouping", **kwargs)
    scope.refresh_from_db()
    assert scope.policy_version == GROUPING_POLICY_VERSION
    _defer(claim)
    scope.refresh_from_db()
    finding = report.findings.get()
    issue = _new_issue(
        scope,
        {
            "mechanism": "Incorrect refund amount",
            "fix_hypothesis": "Use requested amount",
            "falsifier": "Amount matches",
        },
        [str(finding.id)],
    )
    _assign(finding, issue, scope)
    call_command(
        "enable_simulation_grouping",
        **{**kwargs, "expected_registry_revision": scope.registry_revision},
    )
    _ready_features()
    finding.refresh_from_db()
    assert finding.cluster_id == issue.cluster_id
    assert (
        TraceGroupingWork.no_workspace_objects.get(report=report).state == "completed"
    )
    assert claim_grouping_work(worker_id="behavior", limit=1)["claims"] == []


@pytest.mark.django_db(transaction=True)
def test_upgrade_blocks_legacy_feature_claim_after_activity_check(
    simulation_reports, monkeypatch
):
    (report,) = simulation_reports(1, policy=GROUPING_POLICY_VERSION)
    scope = TraceGroupingScope.no_workspace_objects.get(project=report.project)
    legacy = TraceGroupingFeatureJob.no_workspace_objects.get(report=report)
    legacy.state = "pending"
    legacy.not_before = timezone.now()
    legacy.save(update_fields=["state", "not_before"])
    checked, claimed = Event(), Event()
    original_save = TraceGroupingScope.save

    def pause_after_activity_check(instance, *args, **kwargs):
        if (
            instance.pk == scope.pk
            and instance.policy_version == SIMULATION_GROUPING_POLICY_VERSION
        ):
            checked.set()
            assert claimed.wait(10), (
                "Feature claim blocked instead of skipping the locked scope"
            )
        return original_save(instance, *args, **kwargs)

    monkeypatch.setattr(TraceGroupingScope, "save", pause_after_activity_check)

    def concurrent_claim():
        try:
            assert checked.wait(10)
            return claim_feature_jobs(worker_id="racing-feature-worker", limit=1)
        finally:
            claimed.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(concurrent_claim)
        call_command(
            "enable_simulation_grouping",
            project_id=scope.project_id,
            apply=True,
            expected_registry_revision=scope.registry_revision,
            stdout=StringIO(),
        )
        assert result.result(timeout=10) == {"claims": []}
    legacy.refresh_from_db()
    assert legacy.state == "superseded"
    (claim,) = claim_feature_jobs(worker_id="after-upgrade", limit=1)["claims"]
    assert claim["policy_version"] == SIMULATION_FEATURE_POLICY_VERSION
    assert claim["feature_job_id"] != str(legacy.id)


def test_upgrade_rejects_feature_claim_that_acquired_scope_first(simulation_reports):
    (report,) = simulation_reports(1, policy=GROUPING_POLICY_VERSION)
    scope = TraceGroupingScope.no_workspace_objects.get(project=report.project)
    legacy = TraceGroupingFeatureJob.no_workspace_objects.get(report=report)
    legacy.state = "pending"
    legacy.not_before = timezone.now()
    legacy.save(update_fields=["state", "not_before"])
    (claim,) = claim_feature_jobs(worker_id="before-upgrade", limit=1)["claims"]
    with pytest.raises(CommandError, match="Drain active"):
        call_command(
            "enable_simulation_grouping",
            project_id=scope.project_id,
            apply=True,
            expected_registry_revision=scope.registry_revision,
            stdout=StringIO(),
        )
    scope.refresh_from_db()
    legacy.refresh_from_db()
    assert scope.policy_version == GROUPING_POLICY_VERSION
    assert legacy.state == "running"
    assert str(legacy.id) == claim["feature_job_id"]
