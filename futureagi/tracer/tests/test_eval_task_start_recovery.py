"""Create dispatch recovery on a real primary DB with a fake Temporal server."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connections, transaction
from django.utils import timezone
from temporalio.exceptions import WorkflowAlreadyStartedError

from tfc.temporal.eval_tasks import client
from tfc.temporal.schedules.tracer import TRACER_SCHEDULES
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStartRequest
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks import start_requests as service
from tracer.tasks.eval_task_sweeper import reconcile_eval_task_starts

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
_CREATE = "/tracer/eval-task/"


class WorkerDied(BaseException):
    """Stop before normal exception handling, as in a process kill."""


@pytest.fixture
def intent(eval_task):
    at = timezone.now()
    EvalTask.objects.using("default").filter(pk=eval_task.pk).update(last_run=at)
    return EvalTaskStartRequest.objects.using("default").create(task=eval_task)


@pytest.fixture
def transport(monkeypatch):
    start = Mock(return_value="synthetic-workflow")
    describe = Mock(return_value=client.WF_ABSENT)
    monkeypatch.setattr("tracer.views.eval_task.start_eval_task_workflow_sync", start)
    monkeypatch.setattr(client, "start_eval_task_workflow_sync", start)
    monkeypatch.setattr(client, "describe_eval_task_workflow_sync", describe)
    return start, describe


def create(auth_client, project, config, *, run_type="historical"):
    response = auth_client.post(
        _CREATE,
        {
            "project": str(project.pk),
            "evals": [str(config.pk)],
            "name": "start recovery",
            "run_type": run_type,
            "spans_limit": 10,
            "sampling_rate": 100,
            "filters": {},
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    return EvalTaskStartRequest.objects.using("default").get(
        pk=response.json()["result"]["id"]
    )


def tick():
    return reconcile_eval_task_starts._original_func()


def expire(intent):
    # Drive the database-clock expiry predicate without sleeping or relying
    # on the relative clocks of request and worker processes.
    leased = EvalTaskStartRequest.objects.using("default").filter(
        pk=intent.pk, lease_until__isnull=False
    )
    assert leased.update(lease_until=timezone.now() - timedelta(days=1)) == 1


def check(intent, state, attempts):
    intent.refresh_from_db(using="default")
    assert (intent.state, intent.attempts) == (state, attempts)
    scheduled = state == "accepted" and intent.last_outcome in (
        "awaiting_worker",
        "worker_admitted",
        "unconfirmed_beyond_history",
    )
    assert (intent.lease_until is not None) == (state == "claimed" or scheduled)
    assert (intent.claim_token is not None) == (state == "claimed")


def test_every_outcome_fits_the_model_and_database_column(intent):
    field = EvalTaskStartRequest._meta.get_field("last_outcome")
    rows = EvalTaskStartRequest.objects.using("default").filter(pk=intent.pk)
    for code in (
        "requested",
        "send_reserved",
        "reply_unknown",
        "describe_unconfirmed",
        "start_unconfirmed",
        "duplicate_seen",
        "awaiting_worker",
        "worker_admitted",
        "execution_progress",
        "superseded",
        "not_started",
        "unconfirmed_beyond_history",
        "execution_closed_without_progress",
    ):
        assert len(code) <= field.max_length, code
        assert rows.update(last_outcome=code) == 1
        assert rows.values_list("last_outcome", flat=True).get() == code


def test_schedule_is_wired_to_the_activity():
    (schedule,) = (
        s for s in TRACER_SCHEDULES if s.schedule_id == "reconcile-eval-task-starts"
    )
    assert schedule.activity_name == reconcile_eval_task_starts._activity_name
    assert (schedule.interval_seconds, schedule.queue) == (60, "tasks_s")


@pytest.mark.parametrize(
    "execution", [client.WF_ABSENT, client.WF_PROGRESSING, client.WF_CLOSED]
)
def test_reconcile_unconfirmed_create(
    auth_client, project, custom_eval_config, transport, execution
):
    start, describe = transport
    executions = []

    def submit(task, **options):
        assert options["start_once"] is True
        if start.call_count == 1:
            if execution != client.WF_ABSENT:
                executions.append(task.pk)  # Accepted, but the caller lost its reply.
            raise ConnectionError("private transport detail")
        executions.append(task.pk)

    start.side_effect = submit
    intent = create(auth_client, project, custom_eval_config)
    check(intent, "pending", 1)
    # Drop the creating connection. The new worker only sees committed state.
    connections.close_all()
    describe.return_value = execution
    assert tick() == {"candidates": 1}
    describe.assert_called_once_with(
        intent.task_id, timeout=5, distinguish_completion=True
    )
    expected_calls = 2 if execution == client.WF_ABSENT else 1
    task = EvalTask.objects.using("default").get(pk=intent.pk)
    if execution == client.WF_CLOSED:
        check(intent, "exhausted", 1)
        assert intent.last_outcome == "execution_closed_without_progress"
        assert task.status == "failed"
        reason = service.start_request_error(task)
        assert "ended before evaluation began" in reason and "Resume" not in reason
        refused = auth_client.post(
            f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}"
        )
        assert refused.status_code == 400 and "Re-run" in refused.json()["result"]
    else:
        check(intent, "accepted", expected_calls)
        assert task.status == "pending"  # the fake worker hasn't begun
    assert tick() == {"candidates": 0}
    assert start.call_count == expected_calls
    assert executions == [intent.task_id]


@pytest.mark.requires_ee
@pytest.mark.parametrize("first_accepted", [True, False])
def test_restarted_sender_admits_one_execution_and_one_usage_row(
    intent, transport, first_accepted
):
    from ee.usage.models.usage import APICallLog

    start, _ = transport
    executions = []

    def send(task, **kwargs):
        if first_accepted or start.call_count == 2:
            run_id = str(uuid4())
            assert service.admit_start(task.pk, run_id)
            executions.append(run_id)
            APICallLog.objects.create(
                organization=task.project.organization, source_id=str(task.pk), cost=1
            )
            EvalTask.objects.filter(pk=task.pk).update(status="completed")
        raise ConnectionError()  # No sender gets an acknowledgement.

    start.side_effect = send
    service.dispatch_start_request(intent.pk, initial_start=start)
    connections.close_all()
    if not first_accepted:
        tick()  # A separate worker spends the one remaining start reservation.
    expire(intent)
    tick()
    assert tick() == {"candidates": 0}
    assert start.call_count == (1 if first_accepted else 2)
    assert len(executions) == 1
    assert APICallLog.objects.filter(source_id=str(intent.pk)).count() == 1


def test_second_failure_needs_negative_describe_before_exhaustion(
    auth_client, project, custom_eval_config, transport, no_new_usage
):
    start, describe = transport
    start.side_effect = ConnectionError("private transport detail")
    intent = create(auth_client, project, custom_eval_config)
    tick()
    check(intent, "pending", 2)
    assert EvalTask.objects.using("default").get(pk=intent.pk).status == "pending"
    describe.side_effect = TimeoutError("private describe detail")
    tick()
    check(intent, "pending", 2)
    assert intent.last_outcome == "describe_unconfirmed"
    describe.side_effect = None
    tick()
    check(intent, "start_failed", 2)
    assert intent.last_outcome == "not_started"
    assert tick() == {"candidates": 0}
    assert start.call_count == 2
    logs = auth_client.get(f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk})
    assert logs.status_code == 200, logs.content
    data = logs.json()["result"]
    message = "The run couldn't start after an automatic retry. Your task is saved."
    assert data["status"] == "failed"
    assert data["start_time"] is None and data["end_time"] is None
    assert data["total_count"] == data["errors_count"] == 0
    assert data["error_groups"] == [
        {"normalized": message, "sample": message, "count": 1}
    ]
    assert "private" not in logs.content.decode()
    start.reset_mock()
    refused = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert refused.status_code == 400 and refused.json()["result"] == message
    start.assert_not_called()
    check(intent, "start_failed", 2)
    assert not EvalLogger.all_objects.filter(eval_task_id=str(intent.pk)).exists()

    # Only the explicit new request gets a new execution; it never replenishes
    # the original reservation budget or leaves the old reason on the task.
    start.side_effect = None
    rerun = auth_client.patch(
        f"{_CREATE}update_eval_task/",
        {"eval_task_id": str(intent.pk), "edit_type": "fresh_run"},
        format="json",
    )
    assert rerun.status_code == 200, rerun.content
    check(intent, "start_failed", 2)  # The original request retains its final outcome.
    start.assert_called_once_with(intent.task, replace_existing=True)
    assert tick() == {"candidates": 0}
    logs = auth_client.get(f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk})
    assert logs.json()["result"]["error_groups"] == []

    # If the new run really started and was paused, history belongs to the
    # ungated replacement, not to the still-immutable original create.
    EvalTask.objects.filter(pk=intent.pk).update(status="paused")
    describe.return_value = client.WF_LEGACY_CLOSED
    start.reset_mock()
    resumed = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert resumed.status_code == 200, resumed.content
    describe.assert_called_with(intent.pk, timeout=5, distinguish_legacy=True)
    start.assert_called_once_with(intent.task, replace_existing=True)
    check(intent, "start_failed", 2)


def test_crash_after_reservation_spends_attempt_before_network_call(
    intent, transport, monkeypatch
):
    start, describe = transport
    with monkeypatch.context() as m:
        m.setattr(service, "_call", Mock(side_effect=WorkerDied()))
        with pytest.raises(WorkerDied):
            service.dispatch_start_request(intent.pk, initial_start=start)
    start.assert_not_called()
    check(intent, "claimed", 1)
    assert tick() == {"candidates": 0}
    expire(intent)
    start.side_effect = ConnectionError()
    tick()
    check(intent, "pending", 2)
    tick()
    check(intent, "start_failed", 2)
    start.assert_called_once()
    assert describe.call_count == 2


def test_crash_during_reconcile_describe_does_not_strand_or_spend(intent, transport):
    start, describe = transport
    describe.side_effect = WorkerDied()
    with pytest.raises(WorkerDied):
        tick()
    check(intent, "claimed", 0)
    start.assert_not_called()
    expire(intent)
    describe.side_effect = None
    tick()
    check(intent, "accepted", 1)
    start.assert_called_once()


def test_reconciler_wins_before_on_commit_and_replay_cannot_reuse_initial_slot(
    intent, transport
):
    start, _ = transport
    start.side_effect = [ConnectionError(), "workflow"]
    tick()  # API process died after commit, before its first dispatch.
    check(intent, "pending", 1)
    service.dispatch_start_request(intent.pk, initial_start=start)
    start.assert_called_once()
    tick()
    check(intent, "accepted", 2)
    assert start.call_count == 2


@pytest.mark.parametrize("accepted", [False, True])
def test_kill_after_final_reservation_distinguishes_missing_send_from_lost_ack(
    intent, transport, monkeypatch, accepted
):
    start, describe = transport
    start.side_effect = [ConnectionError(), "accepted"]
    service.dispatch_start_request(intent.pk, initial_start=start)
    rpc = service._call

    def killed(function, *args, **kwargs):
        if function is start:
            check(intent, "claimed", 2)
            if accepted:
                rpc(function, *args, **kwargs)
            raise WorkerDied()
        return rpc(function, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(service, "_call", killed)
        with pytest.raises(WorkerDied):
            tick()
    check(intent, "claimed", 2)
    assert tick() == {"candidates": 0}
    expire(intent)
    describe.return_value = client.WF_PROGRESSING if accepted else client.WF_ABSENT
    before = start.call_count
    tick()
    check(intent, "accepted" if accepted else "start_failed", 2)
    assert start.call_count == before == (2 if accepted else 1)
    assert all(call.kwargs["start_once"] for call in start.call_args_list)


def test_reclaimed_start_ignores_a_late_failure_from_old_claim(intent, transport):
    start, describe = transport

    def delayed_reply(task, **kwargs):
        # The first claimant lost its lease while awaiting an accepted start.
        expire(intent)
        describe.return_value = client.WF_CLOSED
        tick()
        check(intent, "exhausted", 1)
        raise ConnectionError()

    start.side_effect = delayed_reply
    service.dispatch_start_request(intent.pk, initial_start=start)
    check(intent, "exhausted", 1)
    assert EvalTask.objects.get(pk=intent.pk).status == "failed"
    start.assert_called_once()


def test_concurrent_reconcilers_only_one_spends_the_retry(
    intent, transport, monkeypatch
):
    start, describe = transport
    start.side_effect = [ConnectionError(), "workflow"]
    service.dispatch_start_request(intent.pk, initial_start=start)
    check(intent, "pending", 1)
    real_dispatch = service.dispatch_start_request
    both_selected = Barrier(2, timeout=10)

    def race(task_id):
        both_selected.wait()  # Separate connections both selected the same row.
        real_dispatch(task_id)

    monkeypatch.setattr(service, "dispatch_start_request", race)

    def worker():
        try:
            return tick()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        workers = [pool.submit(worker) for _ in range(2)]
        assert all(worker.result(timeout=20) == {"candidates": 1} for worker in workers)
    check(intent, "accepted", 2)
    describe.assert_called_once()
    assert start.call_count == 2


@pytest.mark.parametrize("initial", [True, False])
def test_server_duplicate_can_mean_closed_without_progress(intent, transport, initial):
    start, describe = transport
    start.side_effect = WorkflowAlreadyStartedError(
        f"eval-task-{intent.pk}", "Workflow"
    )
    service.dispatch_start_request(intent.pk, initial_start=start if initial else None)
    check(intent, "pending", 1)
    describe.return_value = client.WF_CLOSED
    tick()
    check(intent, "exhausted", 1)
    assert EvalTask.objects.get(pk=intent.pk).status == "failed"
    assert intent.last_outcome == "execution_closed_without_progress"
    start.assert_called_once()
    assert tick() == {"candidates": 0}


@pytest.mark.parametrize("edit_type", ["edit_rerun", "fresh_run"])
def test_save_or_rerun_supersedes_a_committed_start(
    auth_client, project, custom_eval_config, transport, edit_type
):
    start, describe = transport
    start.side_effect = ConnectionError()
    intent = create(auth_client, project, custom_eval_config)

    def replace(task, *, replace_existing):
        assert replace_existing
        check(intent, "void", 1)
        # The new PENDING task is otherwise eligible. A delayed create RPC
        # still cannot enter the workflow on the replacement's configuration.
        assert not service.admit_start(task.pk, str(uuid4()))
        return "replacement"

    start.side_effect = replace
    response = auth_client.patch(
        f"{_CREATE}update_eval_task/",
        {"eval_task_id": str(intent.pk), "edit_type": edit_type},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert start.call_args.kwargs == {"replace_existing": True}
    check(intent, "void", 1)  # committed by Save itself, not inferred on next tick
    assert tick() == {"candidates": 0}
    describe.assert_not_called()
    assert start.call_count == 2  # Create and the user's edit, not an automatic retry.


@pytest.mark.parametrize(
    "marker",
    [
        "paused",
        "deleted",
        "deleted_project",
        "end_time",
        "continuous_cursor",
        "task_logger",
        "retired_task_logger",
        "eval_logger",
        "retired_eval_logger",
    ],
)
def test_changed_task_voids_only_its_request(
    intent, project, custom_eval_config, trace, transport, marker
):
    start, describe = transport
    tasks = EvalTask.all_objects.using("default").filter(pk=intent.pk)
    if marker == "paused":
        tasks.update(status="paused")
    elif marker == "deleted":
        tasks.update(deleted=True)
    elif marker == "deleted_project":
        project.delete()
    elif marker.endswith("task_logger"):
        EvalTaskLogger.objects.using("default").create(
            eval_task_id=intent.pk, deleted=marker.startswith("retired_")
        )
    elif marker.endswith("eval_logger"):
        EvalLogger.objects.using("default").create(
            trace=trace,
            observation_span_id="synthetic-span",
            custom_eval_config=custom_eval_config,
            eval_task_id=str(intent.pk),
            status=EvalEntryStatus.PENDING,
            deleted=marker.startswith("retired_"),
        )
    else:
        tasks.update(**{marker: timezone.now()})
    before = tasks.values().get()
    tick()
    check(intent, "void", 0)
    assert tasks.values().get() == before
    describe.assert_not_called()
    start.assert_not_called()


@pytest.mark.parametrize("attempts", [1, 2])
def test_pause_committing_during_describe_wins(intent, transport, attempts):
    start, describe = transport
    EvalTaskStartRequest.objects.using("default").filter(pk=intent.pk).update(
        attempts=attempts
    )

    def pause(task_id, **kwargs):
        EvalTask.objects.using("default").filter(pk=task_id).update(status="paused")
        return client.WF_ABSENT

    describe.side_effect = pause
    tick()
    check(intent, "void", attempts)
    assert EvalTask.objects.using("default").get(pk=intent.pk).status == "paused"
    start.assert_not_called()


@pytest.mark.parametrize("continuous", [False, True])
def test_delayed_final_send_fenced_before_work(
    intent, transport, monkeypatch, continuous
):
    from temporalio.testing import ActivityEnvironment

    from tfc.temporal.eval_tasks import get_activities, workflows
    from tfc.temporal.eval_tasks.activities import admit_eval_task_start_activity

    EvalTask.objects.filter(pk=intent.pk).update(
        run_type="continuous" if continuous else "historical"
    )
    start, _ = transport
    start.side_effect = ConnectionError()
    service.dispatch_start_request(intent.pk, initial_start=start)
    tick()
    options = start.call_args.kwargs
    tick()
    check(intent, "start_failed", 2)
    activities = []

    async def execute(name, data, **options):
        activities.append(name)
        assert name == "admit_eval_task_start_activity"
        assert admit_eval_task_start_activity in get_activities()
        return await ActivityEnvironment().run(admit_eval_task_start_activity, data)

    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    workflow_type, data = client._select(
        start.call_args.args[0], "tasks_s", gated=options["start_once"]
    )
    result = async_to_sync(workflow_type().run)(data)
    if not continuous:
        assert (result.status, result.processed) == ("start_fenced", 0)
    assert activities == ["admit_eval_task_start_activity"]
    assert EvalTask.objects.get(pk=intent.pk).status == "failed"
    assert not EvalLogger.all_objects.filter(eval_task_id=str(intent.pk)).exists()


def test_delayed_first_send_wins_after_retry_reserved(
    intent, transport, monkeypatch, trace, custom_eval_config
):
    from tfc.temporal.eval_tasks import workflows

    start, describe = transport
    EvalTask.objects.filter(pk=intent.pk).update(run_type="historical")
    run_id = str(uuid4())
    active_run = run_id
    first_input = None
    observed = []

    async def gate(name, data, **options):
        assert name == "admit_eval_task_start_activity"
        return await sync_to_async(service.admit_start)(data.task_id, active_run)

    async def record_work(self, data):
        await sync_to_async(EvalLogger.objects.create)(
            eval_task_id=data.task_id,
            observation_span_id=active_run,
            trace=trace,
            custom_eval_config=custom_eval_config,
            status=EvalEntryStatus.PENDING,
        )

    monkeypatch.setattr(workflows.workflow, "execute_activity", gate)
    monkeypatch.setattr(workflows.HistoricalEvalTaskWorkflow, "_run", record_work)

    def send(task, **options):
        nonlocal first_input, active_run
        # Use the actual input delivered by each send (including any fence).
        run, data = client._select(task, "tasks_s", gated=options["start_once"])
        if first_input is None:
            first_input = data
            raise ConnectionError()  # First send queued, not yet visible to describe.
        intent.refresh_from_db()
        observed.append((intent.state, intent.attempts))
        # First send arrives before the retry reaches Temporal. Its input, not
        # the coalescing retry's input, drives the one running workflow.
        async_to_sync(run().run)(first_input)
        observed.append(service.admit_start(task.pk, run_id))  # lost activity reply
        active_run = str(uuid4())
        observed.append(async_to_sync(run().run)(data).status)

    start.side_effect = send
    service.dispatch_start_request(intent.pk, initial_start=start)
    tick()
    check(intent, "accepted", 2)
    describe.assert_called_once_with(intent.pk, timeout=5, distinguish_completion=True)
    assert observed == [("claimed", 2), True, "start_fenced"]
    assert str(intent.admitted_run_id) == run_id
    assert active_run != run_id
    entry = EvalLogger.objects.get(eval_task_id=str(intent.pk))
    assert entry.observation_span_id == run_id
    assert start.call_count == 2
    expire(intent)
    tick()
    check(intent, "accepted", 2)
    assert intent.last_outcome == "execution_progress"
    assert tick() == {"candidates": 0}


@pytest.mark.parametrize("with_progress", [False, True])
def test_acknowledged_execution_closes_before_next_check(
    intent, transport, with_progress, no_new_usage
):
    from tfc.temporal.eval_tasks.activities import _finalize_task_sync

    start, describe = transport
    EvalTask.objects.filter(pk=intent.pk).update(run_type="historical")
    service.dispatch_start_request(intent.pk, initial_start=start)
    if with_progress:
        assert service.admit_start(intent.pk, str(uuid4()))
    check(intent, "accepted", 1)
    expire(intent)

    def closed(task_id, **kwargs):
        if with_progress:
            assert _finalize_task_sync(str(task_id))["finalized"]
        return client.WF_COMPLETED if with_progress else client.WF_CLOSED

    describe.side_effect = closed
    tick()
    describe.assert_called_once_with(intent.pk, timeout=5, distinguish_completion=True)
    task = EvalTask.objects.get(pk=intent.pk)
    if with_progress:
        check(intent, "accepted", 1)
        assert intent.last_outcome == "execution_progress"
        assert task.status == "completed"
        assert not EvalLogger.all_objects.filter(eval_task_id=str(task.pk)).exists()
        assert service.start_request_error(task) is None
    else:
        check(intent, "exhausted", 1)
        assert intent.last_outcome == "execution_closed_without_progress"
        assert task.status == "failed"
    start.assert_called_once()


def test_successful_workflow_without_local_progress_is_not_a_failed_start(
    intent, transport, no_new_usage
):
    start, describe = transport
    service.dispatch_start_request(intent.pk, initial_start=start)
    expire(intent)
    describe.return_value = client.WF_COMPLETED
    tick()
    check(intent, "accepted", 1)
    assert intent.last_outcome == "execution_completed"
    assert EvalTask.objects.get(pk=intent.pk).status == "pending"
    assert not service.admit_start(intent.pk, str(uuid4()))
    assert tick() == {"candidates": 0}
    start.assert_called_once()


@pytest.mark.parametrize("next_event", ["closed", "cursor", "completed"])
def test_admission_is_watched_until_work_or_confirmed_closure(
    auth_client, intent, transport, next_event
):
    start, describe = transport
    service.dispatch_start_request(intent.pk, initial_start=start)
    run_id = str(uuid4())
    assert service.admit_start(intent.pk, run_id)
    tasks = EvalTask.objects.using("default").filter(pk=intent.pk)
    for result in (TimeoutError(), client.WF_PROGRESSING):
        expire(intent)
        describe.side_effect = [result]
        assert tick() == {"candidates": 1}
        check(intent, "accepted", 1)
        assert intent.last_outcome == "worker_admitted"
        assert tasks.get().status == "running"
        assert service.admit_start(intent.pk, run_id)  # idempotent redelivery
    expire(intent)
    if next_event == "closed":
        # A legacy task log is not an evaluation entry. Admission itself already
        # set start_time; neither is a reason to ignore this confirmed closure.
        EvalTaskLogger.objects.create(eval_task_id=intent.pk)
        describe.side_effect = [client.WF_CLOSED]
        tick()
        check(intent, "exhausted", 1)
        assert tasks.get().status == "failed"
        assert tasks.get().start_time is not None
        logs = auth_client.get(
            f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk}
        )
        assert logs.status_code == 200, logs.content
        groups = logs.json()["result"]["error_groups"]
        assert "ended before evaluation began" in groups[0]["sample"]
        assert service.resume_block_reason(tasks.get()) is None
    else:
        if next_event == "cursor":
            tasks.update(continuous_cursor=timezone.now())
        else:
            tasks.update(status="completed", end_time=timezone.now())
        tick()
        check(intent, "accepted", 1)
        assert intent.last_outcome == "execution_progress"
        expected = "running" if next_event == "cursor" else "completed"
        assert tasks.get().status == expected
        assert describe.call_count == 2
    start.assert_called_once()
    assert tick() == {"candidates": 0}


def test_absent_beyond_history_never_retransmits(intent, transport, no_new_usage):
    start, describe = transport
    start.side_effect = ConnectionError()
    service.dispatch_start_request(intent.pk, initial_start=start)
    old = timezone.now() - service.HISTORY_WINDOW - timedelta(seconds=1)
    EvalTaskStartRequest.objects.filter(pk=intent.pk).update(
        created_at=old, first_reserved_at=old
    )
    tick()
    check(intent, "accepted", 1)
    assert intent.last_outcome == "unconfirmed_beyond_history"
    task = EvalTask.objects.get(pk=intent.pk)
    assert task.status == "pending"
    reason = service.start_request_error(task)
    assert "No workflow history" in reason
    assert "Re-run" not in reason and "Resume" not in reason
    start.assert_called_once()
    describe.assert_called_once()


@pytest.mark.parametrize("outage_first", [False, True])
def test_request_older_than_retention_can_still_make_its_first_send(
    auth_client, project, custom_eval_config, transport, monkeypatch, outage_first
):
    start, describe = transport
    # The real create commits; losing the API process discards its callback.
    with monkeypatch.context() as m:
        m.setattr(transaction, "on_commit", Mock())
        intent = create(auth_client, project, custom_eval_config)
    old = timezone.now() - service.HISTORY_WINDOW - timedelta(days=1)
    EvalTaskStartRequest.objects.filter(pk=intent.pk).update(created_at=old)
    if outage_first:
        describe.side_effect = TimeoutError()
        tick()
        describe.side_effect = None
    check(intent, "pending", 0)
    assert intent.first_reserved_at is None
    start.assert_not_called()
    tick()
    check(intent, "accepted", 1)
    assert intent.first_reserved_at > old + service.HISTORY_WINDOW
    assert tick() == {"candidates": 0}
    start.assert_called_once()


def test_old_request_keeps_retry_after_its_recent_first_send(intent, transport):
    start, _ = transport
    EvalTaskStartRequest.objects.filter(pk=intent.pk).update(
        created_at=timezone.now() - service.HISTORY_WINDOW - timedelta(days=1)
    )
    start.side_effect = [ConnectionError(), "workflow"]
    tick()
    check(intent, "pending", 1)
    first_reservation = intent.first_reserved_at
    tick()
    check(intent, "accepted", 2)
    assert intent.first_reserved_at == first_reservation
    assert start.call_count == 2


@pytest.mark.parametrize("sent", [False, True])
def test_aged_start_explained_on_logs_read_without_any_tick(
    auth_client, intent, transport, sent
):
    start, describe = transport
    if sent:
        start.side_effect = TimeoutError()
        service.dispatch_start_request(intent.pk, initial_start=start)
    rows = EvalTaskStartRequest.objects.filter(pk=intent.pk)
    before = rows.values().get()
    assert before["last_outcome"] == ("reply_unknown" if sent else "requested")

    def logs():
        response = auth_client.get(
            f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk}
        )
        assert response.status_code == 200, response.content
        result = response.json()["result"]
        assert result["status"] == "pending"
        assert result["total_count"] == result["errors_count"] == 0
        return result["error_groups"]

    assert logs() == []
    old = timezone.now() - service.UNCONFIRMED_AFTER - timedelta(seconds=1)
    rows.update(created_at=old)
    before = rows.values().get()
    (group,) = logs()
    assert "contact support" in group["sample"] and "Resume" not in group["sample"]
    response = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert response.status_code == 400
    assert rows.values().get() == before
    assert start.call_count == int(sent)
    describe.assert_not_called()


@pytest.mark.parametrize("eventual", [client.WF_ABSENT, client.WF_PROGRESSING])
def test_describe_deadline_is_durable_and_never_offers_blind_resume(
    auth_client, project, custom_eval_config, transport, eventual
):
    start, describe = transport
    start.side_effect = ConnectionError()
    intent = create(auth_client, project, custom_eval_config)
    EvalTaskStartRequest.objects.filter(pk=intent.pk).update(
        created_at=timezone.now() - service.UNCONFIRMED_AFTER - timedelta(seconds=1)
    )
    describe.side_effect = TimeoutError()
    for _ in range(2):
        tick()
        check(intent, "pending", 1)
        assert intent.last_outcome == "start_unconfirmed"
    logs = auth_client.get(f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk})
    assert logs.status_code == 200, logs.content
    data = logs.json()["result"]
    assert data["status"] == "pending"
    assert data["total_count"] == data["errors_count"] == 0
    assert "keep checking; contact support" in data["error_groups"][0]["sample"]
    resume = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert resume.status_code == 400
    describe.side_effect = None
    describe.return_value = eventual
    tick()
    if eventual == client.WF_ABSENT:
        check(intent, "pending", 2)  # The unused retry was sent, and lost its reply.
        assert start.call_count == 2
        tick()
        check(intent, "start_failed", 2)
    else:
        check(intent, "accepted", 1)
        start.assert_called_once()


@pytest.mark.parametrize("action", ["pause", "resume", "delete", "bulk_delete"])
def test_user_action_itself_voids_start(
    intent, auth_client, transport, monkeypatch, action
):
    start, describe = transport
    tasks = EvalTask.objects.filter(pk=intent.pk)
    if action == "pause":
        tasks.update(status="running")
        monkeypatch.setattr(
            "tracer.views.eval_task.signal_pause_eval_task_workflow", Mock()
        )
        response = auth_client.post(
            f"{_CREATE}pause_eval_task/?eval_task_id={intent.pk}"
        )
    elif action == "resume":
        tasks.update(status="failed", continuous_cursor=timezone.now())

        def replace(task, *, replace_existing):
            assert replace_existing
            check(intent, "void", 0)

        start.side_effect = replace
        response = auth_client.post(
            f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}"
        )
        start.assert_called_once()
    elif action == "bulk_delete":
        response = auth_client.post(
            f"{_CREATE}mark_eval_tasks_deleted/", {"eval_task_ids": [str(intent.pk)]},
            format="json",
        )
    else:
        response = auth_client.delete(f"{_CREATE}{intent.pk}/")
    assert response.status_code in (200, 204), response.content
    check(intent, "void", 0)
    assert tick() == {"candidates": 0}
    describe.assert_not_called()


def test_detail_status_round_trip_revokes_create_before_the_next_tick(
    auth_client, project, custom_eval_config, transport
):
    start, describe = transport
    start.side_effect = TimeoutError()
    intent = create(auth_client, project, custom_eval_config)
    for status in ("paused", "pending"):
        response = auth_client.patch(
            f"{_CREATE}{intent.pk}/", {"status": status}, format="json"
        )
        assert response.status_code == 200, response.content
        assert EvalTask.objects.get(pk=intent.pk).status == status
        check(intent, "void", 1)
    assert not service.admit_start(intent.pk, str(uuid4()))
    assert tick() == {"candidates": 0}
    describe.assert_not_called()
    start.assert_called_once()


@pytest.mark.parametrize(
    ("state", "outcome", "expected_reason"),
    [
        ("start_failed", "not_started", service.START_FAILURE_MESSAGE),
        (
            "exhausted",
            "execution_closed_without_progress",
            "The workflow ended before evaluation began. Your task is saved.",
        ),
    ],
)
def test_detail_echo_keeps_terminal_reason_and_refuses_plain_resume(
    auth_client, intent, transport, no_new_usage, state, outcome, expected_reason
):
    start, describe = transport
    requests = EvalTaskStartRequest.objects.filter(pk=intent.pk)
    requests.update(state=state, last_outcome=outcome, attempts=2)
    EvalTask.objects.filter(pk=intent.pk).update(status="failed")
    before = requests.values().get()
    response = auth_client.patch(
        f"{_CREATE}{intent.pk}/", {"status": "failed", "name": "renamed"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert requests.values().get() == before
    logs = auth_client.get(f"{_CREATE}get_eval_task_logs/", {"eval_task_id": intent.pk})
    assert logs.status_code == 200, logs.content
    data = logs.json()["result"]
    assert (data["status"], data["total_count"]) == ("failed", 0)
    assert data["error_groups"][0]["sample"] == expected_reason
    # The gated create may have closed before admission; not a prior drain.
    describe.return_value = client.WF_CLOSED
    refused = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert refused.status_code == 400, refused.content
    assert not EvalLogger.all_objects.filter(eval_task_id=str(intent.pk)).exists()
    assert tick() == {"candidates": 0}
    start.assert_not_called()


def test_detail_echo_of_pending_preserves_its_live_create(
    auth_client, intent, transport, no_new_usage
):
    rows = EvalTaskStartRequest.objects.filter(pk=intent.pk)
    before = rows.values().get()
    response = auth_client.patch(
        f"{_CREATE}{intent.pk}/", {"status": "pending"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert rows.values().get() == before
    refused = auth_client.post(f"{_CREATE}unpause_eval_task/?eval_task_id={intent.pk}")
    assert refused.status_code == 400, refused.content
    assert not EvalLogger.all_objects.filter(eval_task_id=str(intent.pk)).exists()
    start, _ = transport
    start.assert_not_called()
    assert tick() == {"candidates": 1}
    start.assert_called_once()


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        ("start_failed", "not_started"),
        ("exhausted", "execution_closed_without_progress"),
        ("accepted", "execution_progress"),
        ("accepted", "execution_completed"),
        ("void", "superseded"),
    ],
)
def test_void_for_tasks_never_rewrites_a_finished_intent(intent, state, outcome):
    rows = EvalTaskStartRequest.objects.filter(pk=intent.pk)
    rows.update(state=state, last_outcome=outcome, attempts=2)
    before = rows.values().get()
    EvalTaskStartRequest.void_for_tasks([intent.pk])
    assert rows.values().get() == before
    # The task is Pending again, as after a fresh Re-run. Keeping its history
    # must not let a delayed create worker admit itself to the new run.
    assert not service.admit_start(intent.pk, str(uuid4()))
    assert rows.values().get() == before


def test_detail_delete_locks_task_before_results(intent, auth_client):
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connections["default"]) as captured:
        response = auth_client.delete(f"{_CREATE}{intent.pk}/")
    assert response.status_code == 204
    locks = [q["sql"] for q in captured if "FOR UPDATE" in q["sql"]]

    def lock_index(model):
        table = f'FROM "{model._meta.db_table}"'
        return next(i for i, sql in enumerate(locks) if table in sql)

    assert lock_index(EvalTask) < lock_index(EvalLogger)


@pytest.mark.parametrize("run_type", ["historical", "continuous"])
def test_recovery_uses_current_configuration(
    auth_client, project, custom_eval_config, transport, run_type
):
    start, _ = transport
    start.side_effect = [ConnectionError(), "accepted"]
    intent = create(auth_client, project, custom_eval_config, run_type=run_type)
    if run_type == "continuous":
        # A legacy acceptance after creation raises the floor on an ordinary
        # edit below. Non-null start_time still isn't execution progress.
        EvalTask.objects.filter(pk=intent.pk).update(last_run=timezone.now())
    response = auth_client.patch(
        f"{_CREATE}{intent.pk}/",
        {
            "name": "renamed",
            "sampling_rate": 25,
            "last_run": timezone.now().isoformat(),
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    check(intent, "pending", 1)
    if run_type == "continuous":
        assert EvalTask.objects.get(pk=intent.pk).start_time is not None
    tick()
    check(intent, "accepted", 2)
    recovered = start.call_args.args[0]
    assert (recovered.name, recovered.sampling_rate) == ("renamed", 25)


def test_instant_restart_voids_before_replacing_and_rolls_back_with_task(
    intent, transport
):
    from tracer.views.instant_eval_scale import InstantEvalControlView

    start, _ = transport
    task = intent.task
    task.status = "paused"
    task.save(update_fields=["status"])
    for rollback in (True, False):
        with transaction.atomic():
            InstantEvalControlView()._restart(task)
            check(intent, "void", 0)
            start.assert_not_called()
            transaction.set_rollback(rollback)
        if rollback:
            check(intent, "pending", 0)
            assert EvalTask.objects.get(pk=intent.pk).status == "paused"
    start.assert_called_once_with(task, replace_existing=True)


def test_retired_cron_cannot_bypass_durable_create(intent, transport):
    from tracer.utils.eval_tasks import _bridge_retired_dispatcher

    start, _ = transport
    assert _bridge_retired_dispatcher(intent.task)
    start.assert_not_called()
    check(intent, "pending", 0)


def test_pending_task_without_intent_not_scanned(
    eval_task, transport, monkeypatch, no_new_usage
):
    from tracer.utils.eval import evaluate_trace_observe
    from tracer.utils.eval_tasks import (
        eval_task_cron,
        process_eval_task,
        run_for_processed_spans,
    )

    start, describe = transport
    EvalTask.objects.filter(pk=eval_task.pk).update(start_time=timezone.now())
    enqueue = Mock()
    monkeypatch.setattr(process_eval_task, "delay", enqueue)
    eval_task_cron._original_func()
    enqueue.assert_not_called()
    process_eval_task._original_func(str(eval_task.pk))
    run_for_processed_spans._original_func(
        ["stale-span"], ["stale-config"], str(eval_task.pk)
    )
    evaluate_trace_observe._original_func(eval_task_id=str(eval_task.pk))
    assert not EvalLogger.all_objects.filter(eval_task_id=str(eval_task.pk)).exists()
    assert tick() == {"candidates": 0}
    start.assert_not_called()
    describe.assert_not_called()
    eval_task.refresh_from_db(using="default")
    assert eval_task.status == "pending"
