import json
import uuid
from datetime import UTC, datetime, timedelta
from datetime import timezone as fixed_timezone
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from structlog.testing import capture_logs

from simulate.models import Scenarios
from simulate.models.test_execution import CallExecution
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
)
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import is_trace_sampled
from tracer.services.trace_investigation import (
    InvestigationConflict,
    _evidence_window,
    canonical_wire_result_digest,
    claim_due_investigations,
    publish_investigation,
    record_trace_notifications,
    requeue_unread_investigations,
    update_investigation_attempt,
)
from tracer.tests.test_simulation_investigation import (
    _execution,
    _failure_result,
    _unreadable_result,
)
from tracer.tests.test_trace_investigation_control import (
    _configure,
    _delivery,
    _publish,
    _result,
)

pytestmark = pytest.mark.django_db

WAITING = TraceInvestigationJobState.WAITING
COMPLETED = TraceInvestigationJobState.COMPLETED
CANCELLED = TraceInvestigationJobState.CANCELLED


def _notify(project, trace_id=None, **delivery):
    trace_id = trace_id or uuid.uuid4()
    with override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0):
        record_trace_notifications(
            deliveries=[_delivery(project, trace_id=trace_id, **delivery)]
        )
    return trace_id


def _claim():
    claims = claim_due_investigations(
        worker_id="node-1", engine_version="omega-v1", limit=1
    )["claims"]
    return claims[0] if claims else None


def _job(claim):
    return TraceInvestigationJob.no_workspace_objects.get(id=claim["job_id"])


def _make_due(claim):
    TraceInvestigationJob.no_workspace_objects.filter(id=claim["job_id"]).update(
        not_before=timezone.now()
    )


def _expire_lease(claim):
    TraceInvestigationAttempt.no_workspace_objects.filter(
        id=claim["attempt_id"]
    ).update(lease_expires_at=timezone.now() - timedelta(seconds=1))


def _failed(claim, error_message="gateway_upstream_error:http_502"):
    result = _result(claim, findings=False)
    result.update(execution_status="failed", outcome="unknown", evidence_receipts=[])
    if error_message is not None:
        result["error_message"] = error_message
    result["result_digest"] = canonical_wire_result_digest(result)
    return result


def _fail(claim, error_message="gateway_upstream_error:http_502"):
    _publish(
        idempotency_key=str(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        result=_failed(claim, error_message),
    )
    return _job(claim)


def _complete(claim):
    return _publish(
        idempotency_key=str(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        result=_result(claim),
    )


def _retry_wait(job):
    return round((job.not_before - timezone.now()).total_seconds() / 60)


def _give_up(project, **delivery):
    """Lose three leases in a row on one new notification; return the last claim."""
    _notify(project, **delivery)
    for _ in range(3):
        claim = _claim()
        _expire_lease(claim)
        assert _claim() is None  # the claim poll writes the lease off
        _make_due(claim)
    assert _job(claim).state == CANCELLED
    return claim


def test_transient_failure_runs_again_with_backoff_until_its_attempts_run_out(
    observe_project,
):
    _configure(observe_project)
    _notify(observe_project)

    first = _claim()
    with capture_logs() as logs:
        job = _fail(first)
    assert (job.state, job.generation, _retry_wait(job)) == (WAITING, 2, 1)
    (unread,) = [e for e in logs if e["event"] == "trace_investigation_attempt_unread"]
    assert (unread["failure"], unread["retry_in_seconds"]) == (
        "gateway_upstream_error",
        60,
    )
    # The failed report stays current until a later attempt publishes.
    assert job.current_report.error_message == "gateway_upstream_error:http_502"
    assert _claim() is None

    _make_due(first)
    second = _claim()
    assert second["generation"] == 2
    job = _fail(second, "gateway_transport_failed")
    assert (job.state, job.generation, _retry_wait(job)) == (WAITING, 3, 5)

    _make_due(first)
    job = _fail(_claim(), "investigation_deadline")
    assert (job.state, job.generation) == (COMPLETED, 3)
    # Each failed report replaces the failed one before it.
    assert job.current_report.error_message == "investigation_deadline"
    _make_due(first)
    assert _claim() is None
    assert TraceInvestigationAttempt.no_workspace_objects.filter(job=job).count() == 3


@pytest.mark.parametrize(
    "error_message",
    [
        None,  # a worker that predates failure codes
        "",
        "gateway_upstream_error:http_503",
        "clickhouse_read_failed:code_241",
        "clickhouse_read_failed",
        "investigation_cancelled",
        "structured_output_unparseable",
    ],
)
def test_failure_another_attempt_can_clear_is_retried(observe_project, error_message):
    _configure(observe_project)
    _notify(observe_project)
    job = _fail(_claim(), error_message)
    assert (job.state, job.generation) == (WAITING, 2)


@pytest.mark.parametrize(
    "error_message",
    [
        "structured_output_invalid",
        "missing_violated_requirement",
        "reserved_verifier_budget",
        "call_budget_exhausted",
        "runtime_or_output_validation",
        "a_code_this_backend_does_not_know:http_502",
    ],
)
def test_failure_that_repeats_for_the_same_evidence_is_final(
    observe_project, error_message
):
    _configure(observe_project)
    _notify(observe_project)
    job = _fail(_claim(), error_message)
    assert (job.state, job.generation) == (COMPLETED, 1)
    assert job.current_report.error_message == error_message


def test_job_read_before_keeps_its_retries_when_a_later_attempt_fails(
    observe_project,
):
    _configure(observe_project)
    trace_id = _notify(observe_project)
    for offset in (2, 3):  # two attempts that each leave a usable report
        claim = _claim()
        _publish(
            idempotency_key=str(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            result=_result(claim),
        )
        _notify(observe_project, trace_id=trace_id, offset=offset)

    job = _fail(_claim())

    # The job's third attempt is the first unread one, so the backoff starts over.
    assert (job.state, job.generation, _retry_wait(job)) == (WAITING, 4, 1)


def test_failed_report_does_not_replace_an_earlier_completed_report(observe_project):
    _configure(observe_project)
    trace_id = _notify(observe_project)
    earlier = _complete(_claim())["report_id"]
    _notify(observe_project, trace_id=trace_id, offset=2)

    with mock.patch(
        "tracer.services.grouping.lifecycle.deproject_superseded_report"
    ) as deproject:
        job = _fail(_claim(), "structured_output_invalid")

    assert (job.state, job.generation, job.current_report_id) == (COMPLETED, 2, earlier)
    deproject.assert_not_called()
    reports = dict(
        TraceInvestigationReport.no_workspace_objects.filter(job=job).values_list(
            "execution_status", "is_current"
        )
    )
    assert reports == {"completed": True, "failed": False}
    # The job still holds a good report, so it is not offered for a requeue.
    assert _requeue(observe_project)["unread"] == 0


def test_failure_of_a_superseded_attempt_leaves_the_newer_generation_queued(
    observe_project,
):
    _configure(observe_project)
    trace_id = _notify(observe_project)
    claim = _claim()
    _notify(observe_project, trace_id=trace_id, offset=2)

    with capture_logs() as logs:
        job = _fail(claim)

    # The newer notification already owns the next run: no extra retry, no alarm.
    assert (job.state, job.generation) == (WAITING, 2)
    assert "trace_investigation_attempt_unread" not in {e["event"] for e in logs}


def test_retry_that_succeeds_replaces_the_failed_report(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    first = _claim()
    failed_report = _fail(first).current_report

    _make_due(first)
    retry = _claim()
    receipt = _publish(
        idempotency_key=str(retry["attempt_id"]),
        lease_token=retry["lease_token"],
        result=_result(retry),
    )

    job = _job(first)
    assert (job.state, job.generation) == (COMPLETED, 2)
    assert job.current_report_id == receipt["report_id"]
    assert receipt["grouping_status"] == "pending"
    failed_report.refresh_from_db()
    assert failed_report.is_current is False


def test_dead_lease_runs_again_and_keeps_the_earlier_report_until_superseded(
    observe_project,
):
    _configure(observe_project)
    trace_id = _notify(observe_project)
    first = _claim()
    earlier = _publish(
        idempotency_key=str(first["attempt_id"]),
        lease_token=first["lease_token"],
        result=_result(first),
    )["report_id"]

    _notify(observe_project, trace_id=trace_id, offset=2)
    _expire_lease(_claim())
    assert _claim() is None  # the claim poll expires the lease

    job = _job(first)
    assert (job.state, job.generation, _retry_wait(job)) == (WAITING, 3, 1)
    assert job.current_report_id == earlier
    assert TraceInvestigationReport.no_workspace_objects.get(id=earlier).is_current

    _make_due(first)
    retry = _claim()
    with mock.patch(
        "tracer.services.grouping.lifecycle.deproject_superseded_report"
    ) as deproject:
        successor = _publish(
            idempotency_key=str(retry["attempt_id"]),
            lease_token=retry["lease_token"],
            result=_result(retry),
        )["report_id"]
    deproject.assert_called_once_with(
        old_report_id=earlier, successor_report_id=successor
    )
    assert not TraceInvestigationReport.no_workspace_objects.get(id=earlier).is_current


def test_dead_leases_stop_at_the_attempt_cap(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    states = []
    for _ in range(3):
        claim = _claim()
        _expire_lease(claim)
        assert _claim() is None
        states.append(_job(claim).state)
        _make_due(claim)
    assert states == [WAITING, WAITING, CANCELLED]
    assert _claim() is None
    assert set(
        TraceInvestigationAttempt.no_workspace_objects.values_list("status", flat=True)
    ) == {TraceInvestigationAttemptStatus.EXPIRED}


def test_renewing_an_expired_lease_requeues_the_job(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    claim = _claim()
    _expire_lease(claim)

    with pytest.raises(InvestigationConflict, match="lease has expired"):
        update_investigation_attempt(
            attempt_id=claim["attempt_id"],
            organization_id=claim["organization_id"],
            workspace_id=claim["workspace_id"],
            project_id=claim["project_id"],
            job_id=claim["job_id"],
            lease_token=claim["lease_token"],
            action="renew",
            reason="",
        )

    job = _job(claim)
    assert (job.state, job.generation) == (WAITING, 2)


def test_completed_report_that_lands_after_its_lease_ran_out_is_used(
    observe_project,
):
    _configure(observe_project)
    _notify(observe_project)
    claim = _claim()
    _expire_lease(claim)

    late = _complete(claim)

    job = _job(claim)
    assert late["grouping_status"] == "pending"
    assert (job.state, job.generation) == (COMPLETED, 1)
    assert job.current_report_id == late["report_id"]
    assert _claim() is None


def test_late_report_is_used_when_the_lease_was_written_off_and_a_run_is_queued(
    observe_project,
):
    _configure(observe_project)
    _notify(observe_project)
    claim = _claim()
    _expire_lease(claim)
    assert _claim() is None  # the claim poll writes the lease off and queues a run

    late = _complete(claim)

    job = _job(claim)
    assert late["grouping_status"] == "pending"
    # The queued run stays: it cannot be told apart from a newer notification.
    assert (job.state, job.generation) == (WAITING, 2)
    assert job.current_report_id == late["report_id"]
    # The late attempt counts as a read, so the queued run gets a full backoff.
    _make_due(claim)
    job = _fail(_claim())
    assert (job.state, job.generation, _retry_wait(job)) == (WAITING, 3, 1)
    # A failure of that run leaves the late report in place.
    assert job.current_report_id == late["report_id"]


def test_late_report_does_not_replace_the_report_of_a_later_attempt(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    first = _claim()
    _expire_lease(first)
    assert _claim() is None
    _make_due(first)
    current = _complete(_claim())["report_id"]

    late = _complete(first)

    job = _job(first)
    assert late["grouping_status"] == "stale"
    assert (job.state, job.current_report_id) == (COMPLETED, current)


def test_late_report_completes_a_job_that_had_given_up(observe_project):
    _configure(observe_project)
    claim = _give_up(observe_project)

    late = _complete(claim)

    job = _job(claim)
    assert (job.state, job.current_report_id) == (COMPLETED, late["report_id"])


def test_failed_report_that_lands_late_is_not_used(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    claim = _claim()
    _expire_lease(claim)

    job = _fail(claim)

    assert (job.state, job.generation, job.current_report_id) == (WAITING, 2, None)


def test_claim_carries_a_whole_hour_evidence_window_around_the_root_end(
    observe_project,
):
    _configure(observe_project)
    root_end = datetime(
        2026, 9, 11, 16, 58, 13, tzinfo=fixed_timezone(timedelta(hours=5, minutes=30))
    )
    delivery = _delivery(observe_project)
    delivery["value"]["traces"][0]["root_end_time"] = root_end
    with override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0):
        record_trace_notifications(deliveries=[delivery])

    claim = _claim()

    # 16:58 at +05:30 is 11:28 UTC: the bounds are whole UTC hours, a day each side.
    assert claim["evidence_window"] == {
        "start": datetime(2026, 9, 10, 11, tzinfo=UTC),
        "end": datetime(2026, 9, 12, 12, tzinfo=UTC),
    }
    # A half-hour zone must not move the bounds off the UTC hour.
    assert _evidence_window(root_end) == claim["evidence_window"]


def test_claim_without_a_root_end_carries_no_evidence_window(observe_project):
    _configure(observe_project)
    _notify(observe_project)
    TraceInvestigationJob.no_workspace_objects.update(root_end_time=None)

    assert "evidence_window" not in _claim()


def _unread_project(project):
    """Two unread jobs (one failed for good, one cancelled) and one healthy job."""
    _configure(project)
    _notify(project)
    failed = _fail(_claim(), "structured_output_invalid")
    _notify(project)
    cancelled = _claim()
    update_investigation_attempt(
        attempt_id=cancelled["attempt_id"],
        organization_id=cancelled["organization_id"],
        workspace_id=cancelled["workspace_id"],
        project_id=cancelled["project_id"],
        job_id=cancelled["job_id"],
        lease_token=cancelled["lease_token"],
        action="cancel",
        reason="shutdown",
    )
    _notify(project)
    healthy = _claim()
    _publish(
        idempotency_key=str(healthy["attempt_id"]),
        lease_token=healthy["lease_token"],
        result=_result(healthy),
    )
    return failed, _job(cancelled), _job(healthy)


def _requeue(project, *args):
    out = StringIO()
    call_command(
        "requeue_investigations", "--project-id", str(project.id), *args, stdout=out
    )
    return json.loads(out.getvalue())


def test_requeue_previews_by_default_and_applies_once(observe_project):
    failed, cancelled, healthy = _unread_project(observe_project)

    preview = _requeue(observe_project)
    assert preview == {
        "project_id": str(observe_project.id),
        "sampling_rate": 1.0,
        "scan_version": "omega-v1",
        "unread": 2,
        "selected": 2,
        "outside_sampling": 0,
        "requeued": 0,
    }
    failed.refresh_from_db()
    assert (failed.state, failed.generation) == (COMPLETED, 1)

    applied = _requeue(observe_project, "--apply")
    assert (applied["unread"], applied["requeued"]) == (2, 2)
    for job in (failed, cancelled):
        job.refresh_from_db()
        assert (job.state, job.generation) == (WAITING, 2)
        assert job.not_before <= timezone.now()
    healthy.refresh_from_db()
    assert (healthy.state, healthy.generation) == (COMPLETED, 1)

    again = _requeue(observe_project, "--apply")
    assert (again["unread"], again["requeued"]) == (0, 0)
    assert _claim()["generation"] == 2


@pytest.mark.parametrize("config", [{"sampling_rate": 0.0}, {"enabled": False}, None])
def test_requeue_refuses_a_project_whose_scanning_is_off(observe_project, config):
    failed, _cancelled, _healthy = _unread_project(observe_project)
    configs = TraceScanConfig.no_workspace_objects.filter(project=observe_project)
    if config is None:
        configs.delete()
    else:
        configs.update(**config)

    preview = _requeue(observe_project)
    assert (preview["sampling_rate"], preview["unread"]) == (0.0, 2)
    assert (preview["selected"], preview["outside_sampling"]) == (0, 2)
    with pytest.raises(CommandError, match="scanning is off"):
        _requeue(observe_project, "--apply")
    failed.refresh_from_db()
    assert (failed.state, failed.generation) == (COMPLETED, 1)


def test_requeue_limit_applies_after_the_current_sampling_rate(observe_project):
    _configure(observe_project)
    trace_ids = iter(uuid.uuid4, None)
    dropped = next(t for t in trace_ids if not is_trace_sampled(str(t), 0.5))
    kept, later = (
        next(t for t in trace_ids if is_trace_sampled(str(t), 0.5)) for _ in range(2)
    )
    # The excluded job is the oldest: it must not use up the limit.
    for trace_id in (dropped, kept, later):
        _notify(observe_project, trace_id=trace_id)
        _fail(_claim(), "structured_output_invalid")
    TraceScanConfig.no_workspace_objects.update(sampling_rate=0.5)

    result = _requeue(observe_project, "--apply", "--limit", "1")

    assert (
        result["unread"],
        result["selected"],
        result["outside_sampling"],
        result["requeued"],
    ) == (3, 1, 1, 1)
    states = dict(
        TraceInvestigationJob.no_workspace_objects.values_list("trace_id", "state")
    )
    assert states == {dropped: COMPLETED, kept: WAITING, later: COMPLETED}


def test_requeue_leaves_a_cancelled_job_that_still_holds_a_completed_report(
    observe_project,
):
    _configure(observe_project)
    trace_id = _notify(observe_project)
    _complete(_claim())
    job = _job(_give_up(observe_project, trace_id=trace_id, offset=2))
    assert (job.state, job.current_report.execution_status) == (CANCELLED, "completed")

    assert _requeue(observe_project)["unread"] == 0


def test_requeue_rejects_a_limit_outside_its_bound(observe_project):
    with pytest.raises(CommandError, match="limit must be between"):
        _requeue(observe_project, "--limit", "0")


def _debug_analysis_call(auth_client, organization, workspace):
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    execution, call = _execution(
        organization, workspace, scenario, "run", CallExecution.CallStatus.COMPLETED
    )
    auth_client.post(f"/simulate/test-executions/{execution.id}/debug-analysis/")
    return call


def test_simulation_report_that_lands_late_stays_stale(
    auth_client, organization, workspace
):
    call = _debug_analysis_call(auth_client, organization, workspace)
    (claim,) = claim_due_investigations(
        worker_id="test-worker", engine_version="omega-v1", limit=1
    )["claims"]
    _expire_lease(claim)
    result = _failure_result(claim, call)

    late = publish_investigation(
        idempotency_key=str(claim["attempt_id"]),
        lease_token=claim["lease_token"],
        result=result,
        wire_result_digest=result["result_digest"],
    )

    job = TraceInvestigationJob.no_workspace_objects.get(call_execution=call)
    assert (late["grouping_status"], job.current_report_id) == ("stale", None)


def test_requeue_leaves_simulation_jobs_to_debug_analysis(
    auth_client, organization, workspace
):
    call = _debug_analysis_call(auth_client, organization, workspace)
    for _ in range(2):  # the first failure is read again once; the second is final
        (claim,) = claim_due_investigations(
            worker_id="test-worker", engine_version="omega-v1", limit=1
        )["claims"]
        result = _unreadable_result(claim, call)
        publish_investigation(
            idempotency_key=str(claim["attempt_id"]),
            lease_token=claim["lease_token"],
            result=result,
            wire_result_digest=result["result_digest"],
        )
    job = TraceInvestigationJob.no_workspace_objects.get(call_execution=call)
    assert job.current_report.execution_status == "failed"
    # Debug analysis owns its own single re-read; the trace backoff never applies.
    assert job.state == COMPLETED
    assert TraceInvestigationAttempt.no_workspace_objects.filter(job=job).count() == 2

    result = requeue_unread_investigations(project_id=job.project_id, apply=False)

    assert (result["unread"], result["selected"]) == (0, 0)
