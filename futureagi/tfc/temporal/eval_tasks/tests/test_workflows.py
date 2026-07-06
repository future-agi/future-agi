"""End-to-end tests for the per-task eval workflows using an in-memory Temporal
server (WorkflowEnvironment). The CH-backed reconcile and the eval engine are
stubbed (both covered by PR3/PR4/PR5 unit suites); these tests prove the
*orchestration*: drain-to-completed, continue-as-new, pause, crash-reclaim, and
the per-task concurrency bound.

Run sequentially (no xdist):
    set -a && source .env.test.local && set +a
    uv run pytest tfc/temporal/eval_tasks/tests/test_workflows.py -p no:xdist
"""

import asyncio
import threading
import time
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_eval_task_e2e")]


# =============================================================================
# Service stubs (patched onto the real service modules; the activity sync
# helpers re-import them at call time, so the patch is picked up across threads)
# =============================================================================


def _patch_noop_reconcile(monkeypatch):
    from tracer.services.eval_tasks.reconciler import ReconcileResult

    def _noop(task):
        return ReconcileResult()

    monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", _noop)


def _patch_failing_reconcile(monkeypatch):
    """reconcile raises so the activity exhausts its retries and the run fails."""

    def _boom(task):
        raise RuntimeError("simulated reconcile failure")

    monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", _boom)


def _patch_completing_run_entry(monkeypatch):
    def _complete(entry):
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _complete)


def _patch_run_entry_failing_one(monkeypatch, bad_entry_id):
    """Complete every entry except ``bad_entry_id``, which raises — simulating an
    infra fault that run_entry can't swallow (so the activity exhausts retries)."""

    def _selective(entry):
        if str(entry.id) == str(bad_entry_id):
            raise RuntimeError("simulated infra fault")
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _selective)


class _ConcurrencyTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    def __enter__(self):
        with self._lock:
            self._current += 1
            self.peak = max(self.peak, self._current)

    def __exit__(self, *exc):
        with self._lock:
            self._current -= 1


def _patch_counting_run_entry(monkeypatch, tracker):
    def _counting(entry):
        with tracker:
            time.sleep(0.2)
            EvalLogger.objects.filter(id=entry.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
            return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _counting)


# =============================================================================
# Async ORM helpers
# =============================================================================


@sync_to_async
def _status_counts(task_id):
    qs = EvalLogger.objects.filter(eval_task_id=task_id)
    return {
        "pending": qs.filter(status=EvalEntryStatus.PENDING).count(),
        "running": qs.filter(status=EvalEntryStatus.RUNNING).count(),
        "completed": qs.filter(status=EvalEntryStatus.COMPLETED).count(),
        "errored": qs.filter(status=EvalEntryStatus.ERRORED).count(),
        "total": qs.count(),
    }


@sync_to_async
def _task_status(task_id):
    return EvalTask.objects.get(id=task_id).status


@sync_to_async
def _set_status(task_id, status):
    EvalTask.objects.filter(id=task_id).update(status=status)


@sync_to_async
def _make_running_stale(task_id):
    from datetime import timedelta

    from django.utils import timezone

    EvalLogger.objects.filter(eval_task_id=task_id).update(
        updated_at=timezone.now() - timedelta(seconds=3600), attempts=0
    )


# =============================================================================
# Workflow runners
# =============================================================================


async def _run_historical(env, task_id, **kw):
    from tfc.temporal.eval_tasks import get_activities, get_workflows
    from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
    from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

    queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await env.client.execute_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(task_id=task_id, task_queue=queue, **kw),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
    await asyncio.sleep(0.2)
    await sync_to_async(close_old_connections)()
    return result


# =============================================================================
# Historical
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestHistoricalWorkflow:
    async def test_drains_all_entries_to_completed(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 5)

        result = await _run_historical(
            workflow_environment, str(eval_task.id), batch_size=2, max_concurrent=4
        )

        assert result.status == "completed"
        counts = await _status_counts(str(eval_task.id))
        assert counts["completed"] == 5
        assert counts["pending"] == 0 and counts["running"] == 0
        assert await _task_status(str(eval_task.id)) == EvalTaskStatus.COMPLETED

    async def test_continue_as_new_preserves_work(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 4)

        # CAN after every single-item batch — must still drain all 4 and finish.
        result = await _run_historical(
            workflow_environment,
            str(eval_task.id),
            batch_size=1,
            max_concurrent=1,
            continue_as_new_after_batches=1,
        )

        assert result.status == "completed"
        # processed is carried across every CAN hop — the lifetime total, not
        # just the last segment (which would be 1 here).
        assert result.processed == 4
        counts = await _status_counts(str(eval_task.id))
        assert counts["completed"] == 4
        assert counts["pending"] == 0 and counts["running"] == 0

    async def test_pause_exits_clean_without_claiming(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 3)
        await _set_status(str(eval_task.id), EvalTaskStatus.PAUSED)

        result = await _run_historical(
            workflow_environment, str(eval_task.id), batch_size=2
        )

        assert result.status == "paused"
        counts = await _status_counts(str(eval_task.id))
        assert counts["running"] == 0  # nothing left mid-flight
        assert counts["pending"] == 3  # paused before the first claim

    async def test_pause_signal_stops_launching_mid_batch(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        """A pause that lands while a batch is draining lets the in-flight evals
        finish but launches no new ones — the unstarted entries return to pending
        and the run exits paused, with nothing stranded as running. Without the
        in-drain pause check the whole claimed batch would complete first."""
        from tfc.temporal.eval_tasks import get_activities, get_workflows
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        await sync_to_async(make_pending_entries)(eval_task, 6)
        _patch_noop_reconcile(monkeypatch)

        started = []
        lock = threading.Lock()
        two_inflight = threading.Event()
        release = threading.Event()

        def _blocking(entry):
            with lock:
                started.append(str(entry.id))
                if len(started) >= 2:
                    two_inflight.set()
            release.wait(timeout=15)
            EvalLogger.objects.filter(id=entry.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _blocking)

        env = workflow_environment
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)

        async with Worker(
            env.client,
            task_queue=queue,
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=task_id, task_queue=queue, batch_size=6, max_concurrent=2
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )

            # Two evals are in flight (concurrency bound); pause now. The signal
            # stops new launches; the DB flag lets the loop exit.
            assert await sync_to_async(two_inflight.wait)(10) is True
            await handle.signal("pause")
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            release.set()
            result = await handle.result()

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

        assert result.status == "paused"
        assert len(started) == 2  # only the in-flight pair ran; no new launches
        counts = await _status_counts(task_id)
        assert counts["completed"] == 2
        assert counts["pending"] == 4  # the unstarted entries were requeued
        assert counts["running"] == 0  # nothing stranded

    async def test_crash_leftovers_reclaimed_and_drained(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        # Simulate a prior crashed run: entries stuck RUNNING, stale.
        await sync_to_async(make_pending_entries)(
            eval_task, 3, status=EvalEntryStatus.RUNNING
        )
        await _make_running_stale(str(eval_task.id))

        result = await _run_historical(
            workflow_environment, str(eval_task.id), batch_size=5
        )

        assert result.status == "completed"
        counts = await _status_counts(str(eval_task.id))
        assert counts["completed"] == 3
        assert counts["pending"] == 0 and counts["running"] == 0

    async def test_semaphore_bounds_in_flight(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        tracker = _ConcurrencyTracker()
        _patch_noop_reconcile(monkeypatch)
        _patch_counting_run_entry(monkeypatch, tracker)
        await sync_to_async(make_pending_entries)(eval_task, 6)

        result = await _run_historical(
            workflow_environment, str(eval_task.id), batch_size=6, max_concurrent=2
        )

        assert result.status == "completed"
        assert tracker.peak <= 2  # never exceeded the per-task bound
        assert tracker.peak >= 2  # ...but did run concurrently

    async def test_infra_failure_is_isolated_and_drain_completes(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        entries = await sync_to_async(make_pending_entries)(eval_task, 4)
        bad_id = entries[0].id
        _patch_noop_reconcile(monkeypatch)
        _patch_run_entry_failing_one(monkeypatch, bad_id)

        result = await _run_historical(
            workflow_environment, str(eval_task.id), batch_size=4, max_concurrent=4
        )

        # One entry's activity exhausts its retries, but the drain still finishes:
        # the other three complete and the failed one is marked errored (terminal),
        # never left stranded as running.
        assert result.status == "completed"
        counts = await _status_counts(str(eval_task.id))
        assert counts["completed"] == 3
        assert counts["errored"] == 1
        assert counts["pending"] == 0 and counts["running"] == 0

    async def test_stranded_entry_fails_workflow_not_false_completed(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        """When both run_entry and fail_eval_entry exhaust their retries, the
        entry is left stranded RUNNING and the task can't finalize. The workflow
        must fail loudly rather than report COMPLETED over undrained work (the
        drain loop breaks on an empty *pending* claim, so a naive finalize would
        no-op and the run would otherwise still return completed)."""
        from temporalio.client import WorkflowFailureError
        from temporalio.common import RetryPolicy

        import tfc.temporal.eval_tasks.workflows as wf

        entries = await sync_to_async(make_pending_entries)(eval_task, 4)
        bad_id = entries[0].id
        _patch_noop_reconcile(monkeypatch)
        _patch_run_entry_failing_one(monkeypatch, bad_id)

        # The fail path can't converge it either — mark_terminal raises, so the
        # fail_eval_entry activity also exhausts its retries and the entry is
        # never marked errored; it stays RUNNING.
        def _mark_terminal_raises(*args, **kwargs):
            raise RuntimeError("simulated infra fault (fail path)")

        monkeypatch.setattr(
            "tracer.services.eval_tasks.entries.mark_terminal", _mark_terminal_raises
        )
        # Shrink both retry policies so the two exhaustion paths finish fast.
        one_shot = RetryPolicy(maximum_attempts=1)
        monkeypatch.setattr(wf, "RUN_ENTRY_RETRY_POLICY", one_shot)
        monkeypatch.setattr(wf, "CONTROL_RETRY_POLICY", one_shot)

        with pytest.raises(WorkflowFailureError):
            await _run_historical(
                workflow_environment, str(eval_task.id), batch_size=4, max_concurrent=4
            )

        counts = await _status_counts(str(eval_task.id))
        assert counts["completed"] == 3  # the healthy entries still drained
        assert counts["running"] == 1  # the stranded one, left non-terminal
        # Wrapper persists FAILED to the DB, not just the SA.
        assert await _task_status(str(eval_task.id)) == EvalTaskStatus.FAILED

    async def test_reconcile_failure_marks_task_failed(
        self, workflow_environment, eval_task, monkeypatch
    ):
        """A failed activity (here reconcile) must leave the task FAILED, not
        stuck ``running`` — it was flipped to running by _mark_running."""
        from temporalio.client import WorkflowFailureError
        from temporalio.common import RetryPolicy

        import tfc.temporal.eval_tasks.workflows as wf

        _patch_failing_reconcile(monkeypatch)
        # One-shot so the reconcile activity exhausts its retries fast.
        monkeypatch.setattr(wf, "CONTROL_RETRY_POLICY", RetryPolicy(maximum_attempts=1))

        with pytest.raises(WorkflowFailureError):
            await _run_historical(
                workflow_environment, str(eval_task.id), batch_size=2, max_concurrent=4
            )

        assert await _task_status(str(eval_task.id)) == EvalTaskStatus.FAILED

    async def test_entry_passes_through_running_mid_drain(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        """Catch the transient state: while the eval runs, the entry must be
        observably RUNNING (claim flipped it before run_entry executed), and only
        afterwards COMPLETED — proving pending -> running -> completed in order,
        not just the final state. A barrier inside the stub freezes the entry in
        ``running`` so the test can read the live DB before releasing it."""
        from tfc.temporal.eval_tasks import get_activities, get_workflows
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        [entry] = await sync_to_async(make_pending_entries)(eval_task, 1)
        _patch_noop_reconcile(monkeypatch)

        reached_running = threading.Event()
        release = threading.Event()
        observed = {}

        def _blocking(e):
            observed["status_at_run"] = EvalLogger.objects.get(id=e.id).status
            reached_running.set()
            release.wait(timeout=15)
            EvalLogger.objects.filter(id=e.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _blocking)

        env = workflow_environment
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)

        async with Worker(
            env.client,
            task_queue=queue,
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=task_id, task_queue=queue, batch_size=1, max_concurrent=1
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )

            assert await sync_to_async(reached_running.wait)(10) is True
            mid = await _status_counts(task_id)
            assert mid["running"] == 1 and mid["pending"] == 0 and mid["completed"] == 0

            release.set()
            result = await handle.result()

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

        assert observed["status_at_run"] == EvalEntryStatus.RUNNING  # claim ran first
        assert result.status == "completed"
        final = await _status_counts(task_id)
        assert final["completed"] == 1 and final["running"] == 0

    async def test_task_status_passes_through_running_in_db(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        """The EvalTask.status DB row (the UI's source of truth) must move
        pending -> running -> completed, not jump pending -> completed. A barrier
        in the run_entry stub freezes the drain so the test can read the live row
        and confirm it is RUNNING mid-flight, then COMPLETED once released."""
        from tfc.temporal.eval_tasks import get_activities, get_workflows
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        await sync_to_async(make_pending_entries)(eval_task, 1)
        _patch_noop_reconcile(monkeypatch)
        assert await _task_status(str(eval_task.id)) == EvalTaskStatus.PENDING

        reached_running = threading.Event()
        release = threading.Event()

        def _blocking(e):
            reached_running.set()
            release.wait(timeout=15)
            EvalLogger.objects.filter(id=e.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _blocking)

        env = workflow_environment
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)

        async with Worker(
            env.client,
            task_queue=queue,
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=task_id, task_queue=queue, batch_size=1, max_concurrent=1
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )

            assert await sync_to_async(reached_running.wait)(10) is True
            # Drain is in flight: the DB row must already read RUNNING.
            assert await _task_status(task_id) == EvalTaskStatus.RUNNING

            release.set()
            result = await handle.result()

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

        assert result.status == "completed"
        assert await _task_status(task_id) == EvalTaskStatus.COMPLETED


# =============================================================================
# Continuous
# =============================================================================


@pytest.mark.django_db(transaction=True)
class TestContinuousWorkflow:
    async def test_drains_then_loops_without_finalizing(
        self, workflow_environment, eval_task, make_pending_entries, monkeypatch
    ):
        from tfc.temporal.eval_tasks import get_activities, get_workflows
        from tfc.temporal.eval_tasks.types import ContinuousDrainState
        from tfc.temporal.eval_tasks.workflows import ContinuousEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 3)

        env = workflow_environment
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)

        async with Worker(
            env.client,
            task_queue=queue,
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                ContinuousEvalTaskWorkflow.run,
                ContinuousDrainState(
                    task_id=task_id,
                    task_queue=queue,
                    batch_size=10,
                    poll_interval_seconds=1,
                    continue_as_new_after_batches=1,  # exercise CAN on the loop
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )

            drained = False
            for _ in range(50):
                counts = await _status_counts(task_id)
                if counts["completed"] == 3 and counts["pending"] == 0:
                    drained = True
                    break
                await asyncio.sleep(0.2)

            await handle.cancel()
            try:
                await handle.result()
            except Exception:
                pass

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

        assert drained
        counts = await _status_counts(task_id)
        assert counts["completed"] == 3 and counts["running"] == 0
        # Continuous tasks never auto-complete.
        assert await _task_status(task_id) != EvalTaskStatus.COMPLETED
