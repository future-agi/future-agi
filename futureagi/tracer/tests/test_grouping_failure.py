"""Real PostgreSQL failure/recovery boundaries, with no model or CH calls."""

import copy
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingWork,
)
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    claim_grouping_work,
    update_grouping_attempt,
)
from tracer.services.grouping.failure import reconcile_dead_attempts
from tracer.services.grouping.publish import publish_grouping
from tracer.services.grouping.recovery import requeue_failed_work
from tracer.tests.test_grouping_runtime import _prepare_runtime

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def grouping_settings(settings):
    settings.ERROR_FEED_GROUPING_ENABLED = True
    settings.ERROR_FEED_GROUPING_ALL_PROJECTS = True
    settings.ERROR_FEED_GROUPING_DEBOUNCE_SECONDS = 0
    settings.ERROR_FEED_GROUPING_BUDGET_ENFORCED = True
    settings.ERROR_FEED_GROUPING_PROJECT_BUDGET_USD = "100"
    settings.ERROR_FEED_GROUPING_WORK_BUDGET_USD = "100"
    settings.ERROR_FEED_GROUPING_TENANT_BUDGET_USD = "100"


def claimed(project, monkeypatch):
    report = _prepare_runtime(project, monkeypatch, identity=uuid.uuid4())
    claim = claim_grouping_work(worker_id="recovery-test", limit=1)["claims"][0]
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    return report, claim, attempt


def fail(claim, code="control_timeout"):
    return update_grouping_attempt(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        action="fail",
        failure_code=code,
    )


@pytest.mark.parametrize("bad_lead", [False, True])
def test_invalid_snapshot_parks_only_its_own_work(
    observe_project, monkeypatch, bad_lead
):
    from tracer.queries.grouping import GroupingSnapshotError
    from tracer.services.grouping import control

    first = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    second = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    TraceGroupingWork.no_workspace_objects.filter(report=first).update(
        not_before=timezone.now() - timedelta(seconds=2)
    )
    bad, good = (first, second) if bad_lead else (second, first)
    real_export = control.export_grouping_snapshot

    def export(*, report):
        if report.pk == bad.pk:
            raise GroupingSnapshotError("synthetic invalid snapshot")
        return real_export(report=report)

    monkeypatch.setattr(control, "export_grouping_snapshot", export)
    claim = claim_grouping_work(worker_id="isolate-bad-snapshot", limit=1)["claims"][0]
    assert [s["report"]["id"] for s in claim["pending_snapshots"]] == [str(good.id)]
    bad_work = TraceGroupingWork.no_workspace_objects.get(report=bad)
    good_work = TraceGroupingWork.no_workspace_objects.get(report=good)
    bad.refresh_from_db()
    assert bad_work.state == "failed" and bad_work.failure_code == "snapshot_invalid"
    assert bad.grouping_status == "failed"
    assert good_work.state == "running" and good_work.attempt_number == 1


@pytest.mark.parametrize("socket_timeout", [False, True])
def test_clickhouse_transport_failure_uses_bounded_backoff(
    observe_project, monkeypatch, socket_timeout
):
    from clickhouse_driver.errors import NetworkError, SocketTimeoutError

    from tracer.services.grouping import context

    report = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())

    def reject(**kwargs):
        raise (SocketTimeoutError if socket_timeout else NetworkError)(
            "synthetic transport failure"
        )

    monkeypatch.setattr(context, "build_claim_context", reject)
    assert claim_grouping_work(worker_id="clickhouse-outage", limit=1)["claims"] == []
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    report.refresh_from_db()
    assert work.state == "pending" and work.failure_code == "control_server_error"
    assert work.not_before > timezone.now() and work.attempt_number == 1
    assert report.grouping_status == "pending"
    assert work.attempts.get().state == "cancelled"
    assert not claim_grouping_work(worker_id="too-early", limit=1)["claims"]


def test_transient_failure_releases_cohort_and_backs_off(observe_project, monkeypatch):
    first = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    second = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    claim = claim_grouping_work(worker_id="recovery-test", limit=1)["claims"][0]
    assert len(claim["pending_snapshots"]) == 2
    assert fail(claim)["state"] == "cancelled"
    works = list(
        TraceGroupingWork.no_workspace_objects.filter(report__in=[first, second])
    )
    for work in works:
        assert work.state == "pending" and work.failure_code == "control_timeout"
        assert work.attempt_number == 1
        assert work.not_before > timezone.now()
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"])
    assert attempt.lease_expires_at <= timezone.now()
    deadlines = [w.not_before for w in works]
    fail(claim, "grouping_paused")
    assert (
        list(
            TraceGroupingWork.no_workspace_objects.filter(
                report__in=[first, second]
            ).values_list("not_before", flat=True)
        )
        == deadlines
    )
    assert not claim_grouping_work(worker_id="early", limit=1)["claims"]


def test_fifth_failure_is_visible_and_terminal(observe_project, monkeypatch):
    report, claim, attempt = claimed(observe_project, monkeypatch)
    attempt.attempt_number = 5
    attempt.save(update_fields=["attempt_number"])
    TraceGroupingWork.no_workspace_objects.filter(report=report).update(
        attempt_number=5
    )
    fail(claim)
    report.refresh_from_db()
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    assert work.state == "failed" and work.failure_code == "control_timeout"
    assert report.grouping_status == "failed"
    assert not claim_grouping_work(worker_id="later", limit=1)["claims"]


def test_full_retry_cycle_obeys_each_deadline_and_stops_at_five(
    observe_project, monkeypatch
):
    report = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    clock = [timezone.now()]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    for number in range(1, 6):
        claim = claim_grouping_work(worker_id="cycle", limit=1)["claims"][0]
        work = TraceGroupingWork.no_workspace_objects.get(report=report)
        assert work.attempt_number == number
        fail(claim)
        work.refresh_from_db()
        assert not claim_grouping_work(worker_id="too-early", limit=1)["claims"]
        if number < 5:
            assert work.not_before == clock[0] + timedelta(
                seconds=5 * 2 ** (number - 1)
            )
            clock[0] = work.not_before - timedelta(microseconds=1)
            assert not claim_grouping_work(worker_id="still-early", limit=1)["claims"]
            clock[0] = work.not_before
        else:
            assert work.state == "failed"
            clock[0] += timedelta(days=1)
            assert not claim_grouping_work(worker_id="exhausted", limit=1)["claims"]
    report.refresh_from_db()
    assert report.grouping_status == "failed"
    assert work.attempts.count() == 5


def test_cohort_peers_consume_their_own_allowance(observe_project, monkeypatch):
    first = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    second = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    now = timezone.now()
    TraceGroupingWork.no_workspace_objects.filter(report=first).update(
        not_before=now - timedelta(seconds=2)
    )
    TraceGroupingWork.no_workspace_objects.filter(report=second).update(
        not_before=now - timedelta(seconds=1), attempt_number=4
    )
    claim = claim_grouping_work(worker_id="mixed-history", limit=1)["claims"][0]
    assert len(claim["pending_snapshots"]) == 2
    fail(claim)
    lead = TraceGroupingWork.no_workspace_objects.get(report=first)
    peer = TraceGroupingWork.no_workspace_objects.get(report=second)
    assert lead.attempt_number == 1 and lead.state == "pending"
    assert peer.attempt_number == 5 and peer.state == "failed"


def test_simulation_settling_does_not_override_retry_backoff(
    observe_project, monkeypatch
):
    from simulate.models import Scenarios
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution
    from tracer.models.trace_grouping import TraceGroupingFeature
    from tracer.models.trace_investigation import InvestigationWorkload
    from tracer.queries.grouping import (
        canonical_grouping_source_digest,
        export_grouping_snapshot,
    )
    from tracer.services.grouping import control

    first = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    second = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    run = RunTest.objects.create(
        name="backoff regression",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
    )
    execution = TestExecution.objects.create(run_test=run, status="completed")
    scenario = Scenarios.objects.create(
        name="backoff",
        source="synthetic",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
    )
    call = CallExecution.objects.create(
        test_execution=execution, scenario=scenario, status="completed"
    )
    job = first.job
    job.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    job.test_execution = execution
    job.call_execution = call
    job.trace_id = None
    job.save(
        update_fields=["workload_type", "test_execution", "call_execution", "trace_id"]
    )
    first.workload_type = InvestigationWorkload.SIMULATION_TEST_EXECUTION
    first.test_execution = execution
    first.trace_id = None
    first.observed_span_count = None
    first.observed_call_count = 1
    first.future_arrivals_known = None
    first.save(
        update_fields=[
            "workload_type",
            "test_execution",
            "trace_id",
            "observed_span_count",
            "observed_call_count",
            "future_arrivals_known",
        ]
    )
    TraceGroupingFeature.no_workspace_objects.filter(finding__report=first).update(
        source_digest=canonical_grouping_source_digest(
            export_grouping_snapshot(report=first)
        )
    )
    monkeypatch.setattr(control, "_simulation_run_settling", lambda report: False)
    deadline = timezone.now() + timedelta(seconds=40)
    TraceGroupingWork.no_workspace_objects.filter(report=second).update(
        not_before=deadline, failure_code="control_timeout", attempt_number=3
    )
    claim = claim_grouping_work(worker_id="simulation", limit=1)["claims"][0]
    assert [s["report"]["id"] for s in claim["pending_snapshots"]] == [str(first.id)]
    peer = TraceGroupingWork.no_workspace_objects.get(report=second)
    assert peer.not_before == deadline and peer.state == "pending"


def test_persistent_pause_parks_without_consuming_five_attempts(
    observe_project, monkeypatch
):
    report, claim, _ = claimed(observe_project, monkeypatch)
    fail(claim, "grouping_paused")
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    report.refresh_from_db()
    assert work.state == "failed" and work.attempt_number == 1
    assert work.failure_code == "grouping_paused" and report.grouping_status == "failed"


def test_late_failure_cannot_reopen_successful_publication(
    observe_project, monkeypatch
):
    report, claim, _ = claimed(observe_project, monkeypatch)
    publish_grouping(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        idempotency_key="lost-response",
        snapshot_digest=claim["snapshot_digest"],
        registry_revision=claim["registry_revision"],
        commands=[
            {
                "type": "defer",
                "occurrence_ids": claim["pending_ids"],
                "reason": "No supported assignment",
            }
        ],
        receipt_ids=[],
    )
    assert fail(claim)["state"] == "completed"
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    report.refresh_from_db()
    assert work.state == "completed" and not work.failure_code
    assert report.grouping_status == "completed"


def test_failure_is_authenticated_and_codes_are_allowlisted(
    observe_project, monkeypatch
):
    _, claim, _ = claimed(observe_project, monkeypatch)
    with pytest.raises(GroupingControlError, match="failure code"):
        fail(claim, "raw source or provider text")
    with pytest.raises(GroupingControlError, match="not found"):
        fail({**claim, "lease_token": "wrong"})
    assert (
        TraceGroupingAttempt.no_workspace_objects.get(pk=claim["attempt_id"]).state
        == "claimed"
    )


def test_fenced_old_failure_cannot_change_current_work(observe_project, monkeypatch):
    report, claim, attempt = claimed(observe_project, monkeypatch)
    scope = attempt.work.scope
    scope.lease_fence += 1
    scope.save(update_fields=["lease_fence"])
    fail(claim)
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    assert work.state == "running" and not work.failure_code


@pytest.mark.parametrize("exhausted", [False, True])
def test_crash_cleanup_reaches_final_dead_attempt(
    observe_project, monkeypatch, exhausted
):
    report, _, attempt = claimed(observe_project, monkeypatch)
    attempt.lease_expires_at = timezone.now() - timedelta(seconds=1)
    attempt.attempt_number = 5 if exhausted else 1
    attempt.save(update_fields=["lease_expires_at", "attempt_number"])
    if exhausted:
        TraceGroupingWork.no_workspace_objects.filter(report=report).update(
            state="failed", attempt_number=5
        )
    reconcile_dead_attempts()
    attempt.refresh_from_db()
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    report.refresh_from_db()
    assert attempt.state == "expired" and attempt.failure_code == "lease_expired"
    assert work.state == ("failed" if exhausted else "pending")
    assert report.grouping_status == ("failed" if exhausted else "pending")


@pytest.mark.parametrize(
    "reason,code,state",
    [
        ("candidate context exceeds response bound", "context_budget", "failed"),
        ("candidate hard-constraint window exceeds bound", "context_budget", "failed"),
        (
            "candidate registry changed while building claim",
            "grouping_conflict",
            "pending",
        ),
        ("claimed feature identity is ambiguous", "control_rejected", "failed"),
    ],
)
def test_claim_context_failure_records_and_releases_committed_attempt(
    observe_project, monkeypatch, reason, code, state
):
    report = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    from tracer.services.grouping import context

    def reject(**kwargs):
        raise GroupingConflict(reason)

    monkeypatch.setattr(context, "build_claim_context", reject)
    assert claim_grouping_work(worker_id="recovery-test", limit=1)["claims"] == []
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    report.refresh_from_db()
    assert work.state == state and work.failure_code == code
    assert report.grouping_status == ("failed" if state == "failed" else "pending")
    assert work.attempts.get().state == "cancelled"


def test_recovery_never_resets_unresolved_paid_reservations(
    observe_project, monkeypatch
):
    from tracer.services.grouping.accounting import reserve_call

    report, claim, _ = claimed(observe_project, monkeypatch)
    reserve_call(
        attempt_id=claim["attempt_id"],
        lease_token=claim["lease_token"],
        request_key="ambiguous-paid-call",
        request_digest="sha256:" + "a" * 64,
        max_cost_usd="0.1",
    )
    fail(claim, "gateway_unknown_usage")
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    preview = requeue_failed_work(project_id=observe_project.id)
    assert preview["works"][0]["blocked_reason"] == "unresolved_usage"
    applied = requeue_failed_work(
        project_id=observe_project.id,
        apply=True,
        work_ids=[work.id],
        expected_registry_revision=preview["registry_revision"],
    )
    assert not applied["works"][0]["requeued"]
    work.refresh_from_db()
    assert work.state == "failed" and work.retry_start_attempt == 0


def test_reviewed_recovery_preserves_history_and_grants_one_attempt(
    observe_project, monkeypatch
):
    report, claim, attempt = claimed(observe_project, monkeypatch)
    attempt.checkpoint = {
        "files": {"checkpoint.json": {"status": "complete", "binding": "saved"}}
    }
    attempt.save(update_fields=["checkpoint"])
    checkpoint = copy.deepcopy(attempt.checkpoint)
    fail(claim, "grouping_paused")
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    preview = requeue_failed_work(project_id=observe_project.id)
    assert preview["works"][0]["eligible"] and not preview["works"][0]["requeued"]
    work.refresh_from_db()
    assert work.state == "failed"
    with pytest.raises(GroupingConflict, match="revision"):
        requeue_failed_work(
            project_id=observe_project.id,
            apply=True,
            work_ids=[work.id],
            expected_registry_revision=999,
        )
    result = requeue_failed_work(
        project_id=observe_project.id,
        apply=True,
        work_ids=[work.id],
        expected_registry_revision=preview["registry_revision"],
    )
    assert result["works"][0]["requeued"]
    work.refresh_from_db()
    assert (
        work.retry_start_attempt == 1
        and work.retry_limit == 1
        and work.attempt_number == 1
    )
    attempt.refresh_from_db()
    assert attempt.checkpoint == checkpoint and attempt.attempt_number == 1
    # A fresh claim does not resume the intentionally incomplete test checkpoint.
    next_claim = claim_grouping_work(worker_id="retry", limit=1)["claims"][0]
    fail(next_claim)
    work.refresh_from_db()
    assert work.attempt_number == 2 and work.state == "failed"
    assert work.attempts.count() == 2


def test_recovery_requires_explicit_ids_and_rejects_active_scope(
    observe_project, monkeypatch
):
    report, claim, _ = claimed(observe_project, monkeypatch)
    with pytest.raises(ValueError, match="explicit work IDs"):
        requeue_failed_work(
            project_id=observe_project.id,
            apply=True,
            expected_registry_revision=claim["registry_revision"],
        )
    with pytest.raises(GroupingConflict, match="active"):
        requeue_failed_work(
            project_id=observe_project.id,
            apply=True,
            work_ids=[TraceGroupingWork.no_workspace_objects.get(report=report).id],
            expected_registry_revision=claim["registry_revision"],
        )


@pytest.mark.parametrize("temporary", [True, False])
def test_context_database_failure_retries_only_operational_errors(
    observe_project, monkeypatch, temporary
):
    from django.db import OperationalError, ProgrammingError

    from tracer.services.grouping import context

    report = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())

    def reject(**kwargs):
        raise (OperationalError if temporary else ProgrammingError)(
            "synthetic database failure"
        )

    monkeypatch.setattr(context, "build_claim_context", reject)
    assert claim_grouping_work(worker_id="db-failure", limit=1)["claims"] == []
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    assert work.state == ("pending" if temporary else "failed")
    assert work.failure_code == (
        "control_server_error" if temporary else "grouping_worker_error"
    )


def test_recovery_refuses_stale_features_and_disabled_budgets(
    observe_project, monkeypatch, settings
):
    report, claim, _ = claimed(observe_project, monkeypatch)
    fail(claim, "grouping_paused")
    from tracer.models.trace_grouping import TraceGroupingFeature

    TraceGroupingFeature.no_workspace_objects.filter(finding__report=report).update(
        source_digest="changed"
    )
    assert (
        requeue_failed_work(project_id=observe_project.id)["works"][0]["blocked_reason"]
        == "features_stale_or_missing"
    )
    settings.ERROR_FEED_GROUPING_BUDGET_ENFORCED = False
    assert (
        requeue_failed_work(project_id=observe_project.id)["works"][0]["blocked_reason"]
        == "budget_enforcement_disabled"
    )


def test_context_failure_logs_frames_without_source_text(observe_project, monkeypatch):
    from unittest.mock import Mock

    from tracer.services.grouping import context, control

    _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    logger = Mock()
    monkeypatch.setattr(control, "logger", logger)

    def reject(**kwargs):
        raise RuntimeError("private source evidence must not be logged")

    monkeypatch.setattr(context, "build_claim_context", reject)
    assert claim_grouping_work(worker_id="safe-diagnostics", limit=1)["claims"] == []
    logger.error.assert_called_once()
    (event,) = logger.error.call_args.args
    details = logger.error.call_args.kwargs
    assert event == "grouping_claim_context_failed"
    assert details["exception_type"] == "RuntimeError"
    assert details["failure_code"] == "grouping_worker_error"
    assert details["error_traceback"][-1]["function"] == "reject"
    assert "private source evidence" not in str(details)
