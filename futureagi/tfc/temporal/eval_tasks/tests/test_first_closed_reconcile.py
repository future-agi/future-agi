"""The initial wait changes *when* the existing closed freeze runs, not its cutoff."""

import asyncio
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta

import pytest
from asgiref.sync import sync_to_async
from django.db import OperationalError, connections, transaction
from django.utils import timezone
from temporalio import activity
from temporalio.converter import DataConverter
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import activities, get_activities
from tfc.temporal.eval_tasks.tests.test_workflows import _run_historical
from tfc.temporal.eval_tasks.types import (
    CheckedReapInput,
    CheckedReconcileInput,
    EvalTaskWorkflowInput,
    ReapInput,
    ReconcileActivityInput,
)
from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow
from tracer.models.eval_sampling import EvalSamplingCohort
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks.sampling import service
from tracer.services.instant_eval_rows import RUN_MARKER


def mark_running(task):
    return activities._set_task_status_sync(
        str(task.pk), EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("instant", ["none", "marker", "all-configs", "mixed-configs"])
def test_deadline_uses_creation_not_attempt_or_start_time(
    closed_task, custom_eval_config, settings, instant
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 120
    if instant == "marker":
        closed_task.filters = {RUN_MARKER: True}
        closed_task.save(update_fields=["filters"])
    if instant in ("all-configs", "mixed-configs"):
        custom_eval_config.config = {"instant_eval": True}
        custom_eval_config.save(update_fields=["config"])
        if instant == "mixed-configs":
            custom_eval_config.pk = None
            custom_eval_config.name = "ordinary evaluator"
            custom_eval_config.config = {}
            custom_eval_config.save()
            closed_task.evals.add(custom_eval_config)
    deadline = (closed_task.created_at + timedelta(seconds=120)).isoformat()
    for changed in (True, False):
        ack = mark_running(closed_task)
        assert ack["changed"] is changed
        assert ack["first_reconcile_at"] == (
            None if instant in ("marker", "all-configs") else deadline
        )


# Legacy input dataclasses are unchanged from ae0bf483.
@pytest.mark.parametrize(
    "new,old",
    [
        (CheckedReconcileInput("t", require_running=True), ReconcileActivityInput("t")),
        (CheckedReapInput("t", 45, 2, True, True), ReapInput("t", 45, 2, True)),
    ],
)
async def test_default_converter_preserves_old_fields_and_ignores_opt_in(new, old):
    converter = DataConverter.default
    assert await converter.decode(await converter.encode([new]), [type(old)]) == [old]
    (decoded,) = await converter.decode(await converter.encode([old]), [type(new)])
    assert decoded.require_running is False


def stop_task(task, stop):
    fields = {"deleted": True} if stop == "deleted" else {"status": stop}
    EvalTask.objects.filter(pk=task.pk).update(**fields)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stop", ["paused", "deleted"])
@pytest.mark.parametrize(
    "boundary", ["before-wake", "before-acquire", "before-certify"]
)
def test_guard_at_the_write_not_just_before_the_read(
    closed_task, stop, boundary, monkeypatch, settings
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 15
    mark_running(closed_task)
    t = closed_task.created_at + timedelta(minutes=2)

    def clock():
        assert boundary != "before-wake", "inactive wake started a ClickHouse read"
        if boundary == "before-acquire":
            stop_task(closed_task, stop)
        return t

    def blocks(*_):
        # A member page commits normally. Stop before the locked certification:
        # neither certification, failed-freeze cleanup nor lease release may write.
        yield [("unit", "default", 1.0, 1)]
        stop_task(closed_task, stop)

    monkeypatch.setattr(service, "sampling_clock", clock)
    monkeypatch.setattr(service, "stream_query", blocks)
    if boundary == "before-wake":
        stop_task(closed_task, stop)
    result = activities._reconcile_sync(str(closed_task.pk), require_running=True)
    assert result["inactive_status"] == stop
    qs = EvalSamplingCohort.objects.filter(task_id=closed_task.pk)
    if boundary == "before-certify":
        cohort = qs.get()
        assert not cohort.complete and cohort.lease_token is not None
        assert list(cohort.members.values_list("unit_id", flat=True)) == ["unit"]
    else:
        assert not qs.exists()
    assert not EvalLogger.all_objects.filter(eval_task_id=str(closed_task.pk)).exists()


@pytest.mark.django_db(transaction=True)
def test_legacy_reap_keeps_the_base_pause_race(stale_running_entry):
    task, entry = stale_running_entry
    stop_task(task, "paused")
    result = activities._reap_sync(str(task.pk), 600, 3)
    entry.refresh_from_db()
    assert result["requeued"] == 1
    assert (entry.status, entry.attempts) == (EvalEntryStatus.PENDING, 1)


@pytest.mark.django_db(transaction=True)
def test_task_lock_covers_the_reaper_write(stale_running_entry, monkeypatch):
    from tracer.services.eval_tasks import reaper

    task, entry = stale_running_entry
    mark_running(task)
    original = reaper.reap_stale_running

    def contender():
        try:
            with transaction.atomic():
                EvalTask.objects.select_for_update(nowait=True).get(pk=task.pk)
        finally:
            connections.close_all()

    def inside_reaper(task, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(OperationalError) as blocked:
                pool.submit(contender).result(timeout=5)
        assert blocked.value.__cause__.sqlstate == "55P03"
        return original(task, **kwargs)

    monkeypatch.setattr(reaper, "reap_stale_running", inside_reaper)
    assert (
        activities._reap_sync(str(task.pk), 600, 3, require_running=True)["requeued"]
        == 1
    )
    entry.refresh_from_db()
    assert (entry.status, entry.attempts) == (EvalEntryStatus.PENDING, 1)


def empty_stream(monkeypatch, q):
    monkeypatch.setattr(service, "stream_query", lambda *_: iter(()))
    monkeypatch.setattr(service, "sampling_clock", lambda: q)


async def history_for(env, task):
    return await env.client.get_workflow_handle(f"eval-task-{task.pk}").fetch_history()


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "margin,age,base_status",
    [
        (0, 0, False),
        (120, 180, False),
        (120, 0, False),
        (120, 0, True),
    ],
)
async def test_recorded_timer_and_unchanged_freeze(
    workflow_environment, closed_task, monkeypatch, settings, margin, age, base_status
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = margin
    created = timezone.now() - timedelta(seconds=age)
    await EvalTask.objects.filter(pk=closed_task.pk).aupdate(created_at=created)
    # Deliberately *not* creation + S. Assert the frozen cutoff uses Q - S.
    q = created + timedelta(seconds=1 if base_status else margin + 7)
    empty_stream(monkeypatch, q)
    if base_status:
        # Model the old acknowledgement on the wire, not a patch returning False.
        # The cross-checkout run separately requires a real base activity poller.
        @activity.defn(name="set_eval_task_status_activity")
        async def old_ack(inp: activities.SetStatusInput) -> dict:
            result = await sync_to_async(activities._set_task_status_sync)(
                inp.task_id, inp.status, inp.expected_status
            )
            return {key: result[key] for key in ("task_id", "changed", "status")}

        monkeypatch.setattr(activities, "set_eval_task_status_activity", old_ack)
    result = await _run_historical(workflow_environment, str(closed_task.pk))
    assert result.status == "completed"
    history = await history_for(workflow_environment, closed_task)
    timers = [e for e in history.events if e.HasField("timer_started_event_attributes")]
    fired = [e for e in history.events if e.HasField("timer_fired_event_attributes")]
    (reconcile,) = [
        e
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
        and e.activity_task_scheduled_event_attributes.activity_type.name
        == "reconcile_eval_task_activity"
    ]
    args = json.loads(
        reconcile.activity_task_scheduled_event_attributes.input.payloads[0].data
    )
    if base_status:
        assert args == {"task_id": str(closed_task.pk)}
    else:
        assert args["require_running"] is True
    assert len(timers) == len(fired) == int(margin > age and not base_status)
    deadline = created + timedelta(seconds=margin)
    if timers:
        duration = timers[
            0
        ].timer_started_event_attributes.start_to_fire_timeout.ToTimedelta()
        assert duration > timedelta(seconds=30)
        assert fired[0].event_id < reconcile.event_id
    scheduled_at = reconcile.event_time.ToDatetime(tzinfo=UTC)
    if base_status:
        assert scheduled_at < deadline
    else:
        assert scheduled_at >= deadline
    cohort = await EvalSamplingCohort.objects.aget(task_id=closed_task.pk)
    assert cohort.complete and cohort.frozen_at == q - timedelta(seconds=margin)


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stop", ["paused", "deleted"])
async def test_commit_stop_between_reconcile_and_initial_reap(
    workflow_environment, stale_running_entry, settings, monkeypatch, stop
):
    task, entry = stale_running_entry
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 0
    empty_stream(monkeypatch, timezone.now())
    arrived, release = threading.Event(), threading.Event()
    original_reap = activities._reap_sync

    def barrier(*args, **kwargs):
        assert kwargs["require_running"]
        arrived.set()
        assert release.wait(30)
        return original_reap(*args, **kwargs)

    monkeypatch.setattr(activities, "_reap_sync", barrier)
    run = asyncio.create_task(_run_historical(workflow_environment, str(task.pk)))
    try:
        assert await asyncio.to_thread(arrived.wait, 30)
        cohort = await EvalSamplingCohort.objects.aget(task_id=task.pk)
        assert cohort.complete
        await sync_to_async(stop_task)(task, stop)
    finally:
        release.set()
        result = await run
    assert result.status == "paused"
    await entry.arefresh_from_db()
    assert (entry.status, entry.attempts) == (EvalEntryStatus.RUNNING, 0)
    row = await EvalTask.all_objects.aget(pk=task.pk)
    assert (row.status, row.deleted) == (
        ("running", True) if stop == "deleted" else ("paused", False)
    )
    history = await history_for(workflow_environment, task)
    names = [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]
    assert names[-2:] == ["reconcile_eval_task_activity", "reap_stale_running_activity"]


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_pending_patch_between_running_update_and_ack_reread(
    workflow_environment, closed_task, settings, auth_client, monkeypatch
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 60
    empty_stream(monkeypatch, closed_task.created_at + timedelta(minutes=2))
    task_id = str(closed_task.pk)
    updated, release = threading.Event(), threading.Event()
    set_status = activities._set_task_status_sync

    def after_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith("UPDATE "):
            updated.set()
            assert release.wait(30), "detail PATCH did not release the status reread"
        return result

    def set_status_with_barrier(pk, status, expected_status=None, *args, **kwargs):
        if (pk, status, expected_status) == (task_id, "running", "pending"):
            # Only this activity thread's connection is wrapped. Its real SELECT
            # cannot start until the detail request on a separate connection commits.
            with connections["default"].execute_wrapper(after_update):
                return set_status(pk, status, expected_status, *args, **kwargs)
        return set_status(pk, status, expected_status, *args, **kwargs)

    monkeypatch.setattr(activities, "_set_task_status_sync", set_status_with_barrier)
    queue = f"pending-ack-{uuid.uuid4()}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        activities=get_activities(),
        workflows=[HistoricalEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        created = timezone.now()
        await EvalTask.objects.filter(pk=task_id).aupdate(created_at=created)
        handle = await workflow_environment.client.start_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(task_id, task_queue=queue),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        try:
            assert await asyncio.to_thread(updated.wait, 20), (
                "RUNNING update never arrived"
            )
            primary = EvalTask.objects.using("default")
            assert (await primary.aget(pk=task_id)).status == "running"
            response = await sync_to_async(auth_client.patch)(
                f"/tracer/eval-task/{task_id}/", {"status": "pending"}, format="json"
            )
            assert response.status_code == 200
            assert (await primary.aget(pk=task_id)).status == "pending"
        finally:
            release.set()
        async with asyncio.timeout(90):
            result = await handle.result()
        history = await handle.fetch_history()

    scheduled = {
        e.activity_task_scheduled_event_attributes.activity_type.name: e.event_id
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    }
    (ack,) = [
        e
        for e in history.events
        if e.HasField("activity_task_completed_event_attributes")
        and e.activity_task_completed_event_attributes.scheduled_event_id
        == scheduled["set_eval_task_status_activity"]
    ]
    payload = json.loads(
        ack.activity_task_completed_event_attributes.result.payloads[0].data
    )
    assert payload["changed"] is True and payload["status"] == "pending"
    assert (
        payload["first_reconcile_at"] == (created + timedelta(seconds=60)).isoformat()
    )
    assert result.status == (await primary.aget(pk=task_id)).status == "completed"
    (started,) = [
        e for e in history.events if e.HasField("timer_started_event_attributes")
    ]
    (fired,) = [e for e in history.events if e.HasField("timer_fired_event_attributes")]
    assert (
        ack.event_id
        < started.event_id
        < fired.event_id
        < scheduled["reconcile_eval_task_activity"]
    )
    assert (await EvalSamplingCohort.objects.aget(task_id=task_id)).complete


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stop", ["paused", "deleted", "pending"])
async def test_status_edit_during_recorded_timer(
    workflow_environment, closed_task, settings, stop, auth_client, monkeypatch
):
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 20
    # Empty CH source; the HTTP edit, Temporal timer, locked PG freeze, reap
    # and finalizer are real. PENDING is writable on the ordinary detail route.
    empty_stream(monkeypatch, closed_task.created_at + timedelta(minutes=2))
    q = f"first-closed-{uuid.uuid4()}"
    task_id = str(closed_task.pk)
    async with Worker(
        workflow_environment.client,
        task_queue=q,
        activities=get_activities(),
        workflows=[HistoricalEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        await EvalTask.objects.filter(pk=task_id).aupdate(created_at=timezone.now())
        handle = await workflow_environment.client.start_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(task_id, task_queue=q),
            id=f"eval-task-{task_id}",
            task_queue=q,
        )
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(
                    e.HasField("timer_started_event_attributes") for e in history.events
                ):
                    assert not any(
                        e.HasField("timer_fired_event_attributes")
                        for e in history.events
                    )
                    break
                await asyncio.sleep(0.05)
        if stop == "pending":
            response = await sync_to_async(auth_client.patch)(
                f"/tracer/eval-task/{task_id}/", {"status": "pending"}, format="json"
            )
            assert response.status_code == 200
            assert (await EvalTask.objects.aget(pk=task_id)).status == "pending"
        else:
            await sync_to_async(stop_task)(closed_task, stop)
        assert not any(
            e.HasField("timer_fired_event_attributes")
            for e in (await handle.fetch_history()).events
        )
        async with asyncio.timeout(60):
            result = await handle.result()
        history = await handle.fetch_history()
    assert not await EvalLogger.all_objects.filter(eval_task_id=task_id).aexists()
    row = await EvalTask.all_objects.aget(pk=task_id)
    names = [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]
    initial = [
        "get_workflow_labels_activity",
        "set_eval_task_status_activity",
        "reconcile_eval_task_activity",
    ]
    if stop == "pending":
        assert result.status == row.status == "completed"
        assert (await EvalSamplingCohort.objects.aget(task_id=task_id)).complete
        assert names == initial + [
            "reap_stale_running_activity",
            "get_eval_task_state_activity",
            "claim_eval_batch_activity",
            "finalize_eval_task_activity",
        ]
    else:
        assert result.status == "paused"
        assert not await EvalSamplingCohort.objects.filter(task_id=task_id).aexists()
        assert (row.status, row.deleted) == (
            ("running", True) if stop == "deleted" else ("paused", False)
        )
        assert names == initial
