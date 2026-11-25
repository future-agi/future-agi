"""Cloud qualification on two pinned checkouts, one test DB, one local Temporal server."""

import asyncio
import hashlib
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from pathlib import Path

import pytest
from django.db import connections
from django.utils import timezone
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import activities, get_activities, workflows
from tfc.temporal.eval_tasks.tests import creation_base_poller as poller
from tfc.temporal.eval_tasks.tests.test_historical_creation_replay import (
    REAP,
    RECONCILE,
    STATUS,
    base_workflow_history,
    events,
    exchange,
    replay,
)
from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
from tracer.models.eval_sampling import EvalSamplingCohort
from tracer.models.eval_task import EvalTask
from tracer.services.eval_tasks.sampling.query import sampling_clock

pytestmark = [pytest.mark.multiworker, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def candidate_tree():
    tree = os.environ["HISTORICAL_REPLAY_CANDIDATE_TREE"]
    root = Path(__file__).resolve().parents[5]
    poller.verify_source(root, tree)
    assert tree != poller.BASE_TREE
    for module in (activities, workflows):
        expected = (
            (root / "futureagi")
            .joinpath(*module.__name__.split("."))
            .with_suffix(".py")
        )
        assert Path(module.__file__).resolve() == expected
    return tree


@asynccontextmanager
async def base_worker(client, queue, role, tmp_path, margin):
    base = Path(os.environ["HISTORICAL_REPLAY_BASE_CHECKOUT"]).resolve()
    ready = tmp_path / f"ready-{uuid.uuid4()}"
    db = connections["default"].settings_dict
    assert db["NAME"] == db["TEST"]["NAME"]  # No fallback to the app DB.
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        poller.__file__,
        str(base),
        role,
        client.service_client.config.target_host,
        client.namespace,
        queue,
        str(ready),
        cwd=base / "futureagi",
        stdin=asyncio.subprocess.PIPE,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
            "PG_DB": db["NAME"],
            "PG_TEST_DB": db["NAME"],
            "EVAL_SAMPLING_CLOSED_SETTLE_SECONDS": str(margin),
        },
    )
    try:
        async with asyncio.timeout(90):
            while not ready.exists():
                assert proc.returncode is None, f"base {role} exited: {proc.returncode}"
                await asyncio.sleep(0.1)
        yield
    finally:
        proc.stdin.close()
        try:
            assert await asyncio.wait_for(proc.wait(), 30) == 0
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise


def new_worker(client, queue, tree, *, workflow=False):
    return Worker(
        client,
        task_queue=queue,
        identity=f"new-{tree}",
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
        workflows=[workflows.HistoricalEvalTaskWorkflow] if workflow else [],
        activities=[] if workflow else get_activities(),
    )


async def start(client, queue, task):
    created = timezone.now()
    await EvalTask.objects.filter(pk=task.pk).aupdate(created_at=created)
    handle = await client.start_workflow(
        workflows.HistoricalEvalTaskWorkflow.run,
        EvalTaskWorkflowInput(str(task.pk), task_queue=queue),
        id=f"eval-task-{task.pk}",
        task_queue=queue,
    )
    return handle, created


async def until(handle, condition):
    async with asyncio.timeout(90):
        while True:
            history = await handle.fetch_history()
            if condition(history):
                return history
            await asyncio.sleep(0.1)


def save(label, history, handle, tree, created, **observations):
    out = Path(os.environ["HISTORICAL_REPLAY_HISTORY_DIR"])
    data = history.to_json().encode()
    provenance = {
        "base": poller.BASE,
        "candidate_tree": tree,
        "created_at": created.isoformat(),
        "workflow_id": handle.id,
        "run_id": handle.first_execution_run_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        **observations,
    }
    with (out / f"{label}-history.json").open("xb") as f:
        f.write(data)
    with (out / f"{label}-provenance.json").open("x") as f:
        json.dump(provenance, f, indent=2)
    return out


async def test_capture_base_workflow_new_status(
    workflow_environment, closed_task, candidate_tree, tmp_path, settings
):
    client = workflow_environment.client
    queue = f"base-wf-{uuid.uuid4()}"
    settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 120
    async with (
        base_worker(client, queue, "workflow", tmp_path, 120),
        new_worker(client, queue, candidate_tree),
    ):
        handle, created = await start(client, queue, closed_task)
        async with asyncio.timeout(120):
            assert (await handle.result()).status == "completed"
        history = await handle.fetch_history()
    out = save("mixed", history, handle, candidate_tree, created)
    # Validate the saved bytes, including positive remaining duration and source identities.
    await replay(base_workflow_history("mixed", out))


async def test_base_handlers_after_candidate_timer(
    workflow_environment, stale_running_entry, candidate_tree, tmp_path, settings
):
    client = workflow_environment.client
    queue = f"base-activities-{uuid.uuid4()}"
    margin = settings.EVAL_SAMPLING_CLOSED_SETTLE_SECONDS = 30
    task, entry = stale_running_entry
    async with new_worker(client, queue, candidate_tree, workflow=True):
        async with new_worker(client, queue, candidate_tree):
            handle, created = await start(client, queue, task)
            hist = await until(handle, lambda h: events(h, "timer_started"))
            assert not events(hist, "timer_fired")
        # No activity poller now. Let the new workflow schedule its first
        # flagged command, then stop workflow polling before enabling BASE.
        hist = await until(
            handle,
            lambda h: any(
                e.activity_task_scheduled_event_attributes.activity_type.name
                == RECONCILE
                for e in events(h, "activity_task_scheduled")
            ),
        )
    scheduled_id = events(hist, "activity_task_scheduled")[-1].event_id
    ch_before = await asyncio.to_thread(sampling_clock)
    async with base_worker(client, queue, "activities", tmp_path, margin):
        hist = await until(
            handle,
            lambda h: any(
                e.activity_task_completed_event_attributes.scheduled_event_id
                == scheduled_id
                for e in events(h, "activity_task_completed")
            ),
        )
        cohort = await EvalSamplingCohort.objects.aget(task_id=task.pk)
        ch_after = await asyncio.to_thread(sampling_clock)
        assert cohort.complete
        assert ch_before <= cohort.frozen_at + timedelta(seconds=margin) <= ch_after
        await entry.arefresh_from_db()
        before = [entry.status, entry.attempts]
        assert before == ["running", 0]
        assert (
            await EvalTask.objects.filter(pk=task.pk, status="running").aupdate(
                status="paused"
            )
            == 1
        )
        # The old activity poller cannot take reap before the pause commits:
        # only now can a workflow worker consume the reconcile completion.
        async with new_worker(client, queue, candidate_tree, workflow=True):
            async with asyncio.timeout(60):
                result = await handle.result()
            hist = await handle.fetch_history()
    await task.arefresh_from_db()
    await entry.arefresh_from_db()
    after = [entry.status, entry.attempts]
    assert result.status == task.status == "paused" and after == ["pending", 1]
    _, _, ack = exchange(hist, STATUS, f"new-{candidate_tree}")
    deadline = created + timedelta(seconds=margin)
    assert ack["first_reconcile_at"] == deadline.isoformat()
    old = f"base-{poller.BASE}"
    reconcile, args, _ = exchange(hist, RECONCILE, old)
    reap, reap_args, counts = exchange(hist, REAP, old)
    assert args == {"task_id": str(task.pk), "require_running": True}
    assert reap_args["require_running"] is True and reap_args["task_id"] == str(task.pk)
    assert counts == {"requeued": 1, "failed": 0}
    (timer,) = events(hist, "timer_started")
    (fired,) = events(hist, "timer_fired")
    assert fired.timer_fired_event_attributes.started_event_id == timer.event_id
    assert fired.event_id < reconcile.event_id < reap.event_id
    assert reconcile.event_time.ToDatetime(tzinfo=UTC) >= deadline
    assert {
        e.workflow_task_completed_event_attributes.identity
        for e in events(hist, "workflow_task_completed")
    } == {f"new-{candidate_tree}"}
    assert not events(hist, "workflow_task_failed")
    save(
        "posttimer",
        hist,
        handle,
        candidate_tree,
        created,
        entry_before_pause=before,
        entry_after_reap=after,
        task_status=task.status,
        workflow_status=result.status,
        cohort_id=str(cohort.pk),
        frozen_at=cohort.frozen_at.isoformat(),
        ch_before=ch_before.isoformat(),
        ch_after=ch_after.isoformat(),
    )
