"""Stranded-work recovery composed end to end on an in-memory Temporal server.

The sweep's unit tests replace the whole starter with a recorder, so they stop
at "a start was issued" and never see the drain that follows. These drive the
real path instead: ``recover_task`` describes and starts through the real
client helpers (pointed at the test server), the real workflow runs its real
reap/claim/finalize activities against Postgres, and only the CH-backed
reconcile and the eval engine are stubbed, as in ``test_workflows.py``.

The case they pin: a workflow stopped while some of its entries were claimed,
so they sit ``RUNNING`` with a stamp younger than the 600 s a restarted
workflow's reap applies. Restarting then claimed nothing, could not finalize,
and marked the task FAILED, which the sweep never looks at again.

Run sequentially (no xdist):
    uv run pytest tfc/temporal/eval_tasks/tests/test_stranded_recovery.py \\
        -m e2e -p no:xdist
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.utils import timezone
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_eval_task_e2e")]

# The queue ``start_eval_task_workflow_sync`` starts on when the sweep calls it.
_SWEEP_QUEUE = "tasks_s"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def temporal_env(workflow_environment):
    """The in-memory server with the eval-task Search Attributes registered.

    Every workflow upserts them on its first task, and upserting an
    unregistered attribute wedges the workflow task forever, so without this
    the module hangs whenever it runs without ``test_observability`` first.
    """
    from tfc.temporal.eval_tasks.registration import register_search_attributes

    await register_search_attributes(
        workflow_environment.client, workflow_environment.client.namespace
    )
    return workflow_environment


@pytest_asyncio.fixture
async def no_leftover_workflow(temporal_env, eval_task):
    """Terminate the task's workflow if a failing test left it running, so it
    cannot keep retrying against a flushed database on the shared queue."""
    yield
    try:
        await temporal_env.client.get_workflow_handle(
            f"eval-task-{eval_task.id}"
        ).terminate("test teardown")
    except Exception:
        pass


def _patch_noop_reconcile(monkeypatch):
    from tracer.services.eval_tasks.reconciler import ReconcileResult

    monkeypatch.setattr(
        "tracer.services.eval_tasks.reconciler.reconcile",
        lambda _task: ReconcileResult(),
    )


def _patch_completing_run_entry(monkeypatch):
    def _complete(entry):
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _complete)


def _point_the_client_at(monkeypatch, env):
    """Route the product's Temporal client helpers to the test server.

    ``get_client`` caches one client per event loop and the sync helpers run
    on a private bridge loop, so the stand-in connects a client of its own on
    whichever loop asks rather than handing over the fixture's.
    """
    target = env.client.service_client.config.target_host
    namespace = env.client.namespace

    async def _connect():
        return await Client.connect(target, namespace=namespace)

    monkeypatch.setattr("tfc.temporal.common.client.get_client", _connect)


def _shorten_finalize_wait(monkeypatch):
    import tfc.temporal.eval_tasks.workflows as wf

    monkeypatch.setattr(wf, "_FINALIZE_WAIT", timedelta(seconds=1), raising=False)


@sync_to_async
def _age(entry_ids, seconds):
    EvalLogger.all_objects.filter(id__in=entry_ids).update(
        updated_at=timezone.now() - timedelta(seconds=seconds)
    )


@sync_to_async
def _entries(task_id):
    return list(
        EvalLogger.all_objects.filter(eval_task_id=task_id)
        .order_by("id")
        .values_list("status", "attempts")
    )


@sync_to_async
def _task_status(task_id):
    return EvalTask.objects.get(id=task_id).status


@sync_to_async
def _set_task(task_id, **fields):
    EvalTask.objects.filter(id=task_id).update(**fields)


@sync_to_async(thread_sensitive=False)
def _recover(task_id):
    """One sweep tick's work for one task, on a thread with no running loop —
    the sync Temporal helpers refuse to run on one."""
    from tracer.tasks.eval_task_sweeper import recover_task

    try:
        task = EvalTask.objects.get(id=task_id)
        return recover_task(task, stale_running_seconds=7_200)
    finally:
        close_old_connections()


@sync_to_async(thread_sensitive=False)
def _sweepable(task_id):
    from tracer.tasks.eval_task_sweeper import find_stranded_tasks

    try:
        return any(str(t.id) == task_id for t in find_stranded_tasks(limit=1_000))
    finally:
        close_old_connections()


async def _workflow_exists(env, task_id):
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await env.client.get_workflow_handle(f"eval-task-{task_id}").describe()
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return True


async def _wait_for(predicate, *, seconds=15.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.2)
    return False


def _historical_handle(env, task_id):
    from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

    return env.client.get_workflow_handle_for(
        HistoricalEvalTaskWorkflow.run, f"eval-task-{task_id}"
    )


def _worker(env, queue):
    from tfc.temporal.eval_tasks import get_activities, get_workflows

    return Worker(
        env.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_leftover_workflow")
class TestSweepRecoversFreshlyAbandonedClaims:
    @pytest.mark.parametrize("age_seconds", [60, 300, 599, 601, 1_000])
    async def test_sweep_restarts_only_once_the_claim_is_reclaimable(
        self,
        temporal_env,
        eval_task,
        make_pending_entries,
        monkeypatch,
        age_seconds,
    ):
        """Sweep → describe → (defer | start) → reap → drain → finalize.

        Below 600 s the tick defers: nothing is started and the task stays
        RUNNING and sweepable. Once the claim is past 600 s a tick restarts
        it, and the restarted workflow reclaims the entry and completes.
        """
        env = temporal_env
        _point_the_client_at(monkeypatch, env)
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        task_id = str(eval_task.id)
        [entry] = await sync_to_async(make_pending_entries)(
            eval_task, 1, status=EvalEntryStatus.RUNNING
        )
        await _set_task(task_id, status=EvalTaskStatus.RUNNING)
        await _age([entry.id], age_seconds)

        async with _worker(env, _SWEEP_QUEUE):
            outcome = await _recover(task_id)
            if age_seconds < 600:
                assert outcome.get("deferred") is True, outcome
                assert outcome["restarted"] is False
                assert await _workflow_exists(env, task_id) is False
                assert await _task_status(task_id) == EvalTaskStatus.RUNNING
                assert await _entries(task_id) == [(EvalEntryStatus.RUNNING, 0)]
                assert await _sweepable(task_id) is True

                await _age([entry.id], 601)
                outcome = await _recover(task_id)

            assert not outcome.get("deferred")
            assert outcome["restarted"] is True
            result = await _historical_handle(env, task_id).result()

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()
        assert result.status == "completed"
        assert await _task_status(task_id) == EvalTaskStatus.COMPLETED
        assert await _entries(task_id) == [(EvalEntryStatus.COMPLETED, 1)]

    async def test_a_restart_for_pending_work_waits_out_a_fresh_claim(
        self, temporal_env, eval_task, make_pending_entries, monkeypatch
    ):
        """Pending work is claimable, so the tick restarts at once — beside an
        entry claimed 300 s ago. The workflow drains the pending entry, cannot
        finalize over the fresh claim, and must wait rather than fail: the
        task stays RUNNING until the claim is reclaimable, then completes."""
        env = temporal_env
        _point_the_client_at(monkeypatch, env)
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        _shorten_finalize_wait(monkeypatch)
        task_id = str(eval_task.id)
        await sync_to_async(make_pending_entries)(eval_task, 1)
        [fresh] = await sync_to_async(make_pending_entries)(
            eval_task, 1, status=EvalEntryStatus.RUNNING
        )
        await _set_task(task_id, status=EvalTaskStatus.RUNNING)
        await _age([fresh.id], 300)

        async with _worker(env, _SWEEP_QUEUE):
            outcome = await _recover(task_id)
            assert outcome["restarted"] is True
            handle = _historical_handle(env, task_id)

            async def _pending_drained():
                statuses = [status for status, _ in await _entries(task_id)]
                return EvalEntryStatus.PENDING not in statuses

            assert await _wait_for(_pending_drained)
            # Several finalize waits pass with the claim still fresh.
            await asyncio.sleep(3)
            assert await _task_status(task_id) == EvalTaskStatus.RUNNING
            assert (await handle.describe()).status.name == "RUNNING"

            await _age([fresh.id], 601)
            result = await handle.result()

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()
        assert result.status == "completed"
        assert await _task_status(task_id) == EvalTaskStatus.COMPLETED
        assert sorted(await _entries(task_id)) == [
            (EvalEntryStatus.COMPLETED, 0),
            (EvalEntryStatus.COMPLETED, 1),
        ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_leftover_workflow")
class TestContinuousRestartReapsClaimsThatAge:
    async def test_a_claim_too_fresh_for_the_first_reap_is_reaped_later(
        self, temporal_env, eval_task, make_pending_entries, monkeypatch
    ):
        """A continuous restart reaps once before its first claim. An entry
        claimed 300 s before is too fresh then, and the loop used to poll
        without reaping again until a continue-as-new, while the sweep saw a
        live workflow and left it alone. It must be reaped once it ages."""
        from tfc.temporal.eval_tasks.types import ContinuousDrainState
        from tfc.temporal.eval_tasks.workflows import ContinuousEvalTaskWorkflow

        env = temporal_env
        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        task_id = str(eval_task.id)
        [fresh] = await sync_to_async(make_pending_entries)(
            eval_task, 1, status=EvalEntryStatus.RUNNING
        )
        await _set_task(
            task_id, status=EvalTaskStatus.RUNNING, run_type=RunType.CONTINUOUS
        )
        await _age([fresh.id], 300)
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"

        async with _worker(env, queue):
            handle = await env.client.start_workflow(
                ContinuousEvalTaskWorkflow.run,
                ContinuousDrainState(
                    task_id=task_id,
                    task_queue=queue,
                    poll_interval_seconds=1,
                    workflow_confirmed_stopped=True,
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            await asyncio.sleep(3)
            assert await _entries(task_id) == [(EvalEntryStatus.RUNNING, 0)]

            await _age([fresh.id], 601)

            async def _completed():
                return await _entries(task_id) == [(EvalEntryStatus.COMPLETED, 1)]

            reaped = await _wait_for(_completed)
            # Read before the cancel: a cancel that lands mid-activity reaches
            # the run's catch-all and marks the task failed, which says
            # nothing about the reap.
            status_while_draining = await _task_status(task_id)
            await handle.cancel()
            try:
                await handle.result()
            except Exception:
                pass

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()
        assert reaped
        assert status_while_draining == EvalTaskStatus.RUNNING


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_leftover_workflow")
class TestIdleContinuousCatchup:
    @pytest.mark.parametrize(
        "status",
        [EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING, EvalTaskStatus.FAILED],
    )
    @pytest.mark.parametrize("has_cursor", [False, True])
    async def test_restart_preserves_coverage_across_partial_catchup_slices(
        self,
        temporal_env,
        eval_task,
        make_pending_entries,
        monkeypatch,
        settings,
        status,
        has_cursor,
    ):
        """Real describe/start, workflow, state activity, materializer and cursor.

        Only CH's row proof/metadata and the evaluator are doubled. Two proven
        prefixes predate the restart by days: resetting start_time to now must
        not clamp the first prefix past the second and silently lose its rows.
        """
        from tfc.temporal.eval_tasks import client
        from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
        from tracer.services.eval_tasks.config_hash import resolved_config_hash
        from tracer.services.eval_tasks.cursor_policy import CONTINUOUS_CURSOR_OVERLAP

        env = temporal_env
        _point_the_client_at(monkeypatch, env)
        _patch_completing_run_entry(monkeypatch)
        settings.EVAL_TASK_SWEEP_RECOVER_FAILED = status == EvalTaskStatus.FAILED
        task_id = str(eval_task.id)
        floor = timezone.now() - timedelta(days=2)
        rows = await sync_to_async(make_pending_entries)(eval_task, 3)
        first_span, second_span = [r.observation_span_id for r in rows[1:]]

        class SpanMetadata:
            def list_by_ids(self, ids, *, project_id, columns):
                assert project_id == str(eval_task.project_id)
                return [
                    {"id": r.observation_span_id, "trace_id": str(r.trace_id)}
                    for r in rows
                    if r.observation_span_id in ids
                ]

            def close(self):
                pass

        monkeypatch.setattr(
            "tracer.services.eval_tasks.entries.get_reader", SpanMetadata
        )

        @sync_to_async
        def prepare():
            EvalLogger.all_objects.filter(id__in=[r.id for r in rows[1:]]).delete()
            cfg = eval_task.evals.get()
            EvalLogger.objects.filter(id=rows[0].id).update(
                status=EvalEntryStatus.COMPLETED, config_hash=resolved_config_hash(cfg)
            )
            EvalTask.objects.filter(id=eval_task.id).update(
                status=status,
                run_type=RunType.CONTINUOUS,
                created_at=floor,
                start_time=floor if has_cursor else None,
                continuous_cursor=floor if has_cursor else None,
            )
            return EvalLogger.objects.get(id=rows[0].id).updated_at

        completed_at = await prepare()
        seen_floors = []

        def bounded_proof(task, *, ceiling):
            current_floor = task.continuous_cursor or task.start_time or task.created_at
            seen_floors.append(current_floor)
            if current_floor < floor + timedelta(hours=3):
                spans = (first_span,)
                covered = floor + timedelta(hours=3) + CONTINUOUS_CURSOR_OVERLAP
            elif current_floor < floor + timedelta(hours=6):
                spans = (second_span,)
                covered = floor + timedelta(hours=6) + CONTINUOUS_CURSOR_OVERLAP
            else:
                spans, covered = (), ceiling
            return ResolvedRowSet(
                spans, spans, task.continuous_cursor is None, covered_through=covered
            )

        monkeypatch.setattr(
            "tracer.services.eval_tasks.reconciler.resolve_desired_rows", bounded_proof
        )
        select = client._select

        def quick_poll(*args, **kwargs):
            workflow_class, state = select(*args, **kwargs)
            state.poll_interval_seconds = 1
            return workflow_class, state

        monkeypatch.setattr(client, "_select", quick_poll)

        async with _worker(env, _SWEEP_QUEUE):
            outcome = await _recover(task_id)
            assert outcome["restarted"]

            async def all_completed():
                return await _entries(task_id) == [(EvalEntryStatus.COMPLETED, 0)] * 3

            completed = await _wait_for(all_completed, seconds=30)
            assert seen_floors[:2] == [floor, floor + timedelta(hours=3)]
            assert completed
            live = await _recover(task_id)
            assert live["progressing"] and not live["restarted"]
            await env.client.get_workflow_handle(f"eval-task-{task_id}").terminate(
                "test complete"
            )

        @sync_to_async
        def check_preserved():
            task = EvalTask.objects.get(id=task_id)
            assert task.start_time == floor
            assert task.continuous_cursor >= floor + timedelta(hours=6)
            assert EvalLogger.objects.get(id=rows[0].id).updated_at == completed_at

        await check_preserved()

    async def test_concurrent_idle_recoveries_coalesce_on_one_live_execution(
        self, temporal_env, eval_task, monkeypatch
    ):
        env = temporal_env
        _point_the_client_at(monkeypatch, env)
        task_id = str(eval_task.id)
        await _set_task(
            task_id, status=EvalTaskStatus.PENDING, run_type=RunType.CONTINUOUS
        )
        assert await _sweepable(task_id)
        # No worker yet: both starts can race, but neither may replace the
        # execution the other just started under this per-task workflow id.
        outcomes = await asyncio.gather(_recover(task_id), _recover(task_id))
        assert any(o["restarted"] for o in outcomes)
        handle = env.client.get_workflow_handle(f"eval-task-{task_id}")
        before = await handle.describe()
        again = await _recover(task_id)
        after = await handle.describe()
        assert again["progressing"] and not again["restarted"]
        assert before.run_id == after.run_id
        assert after.status.name == "RUNNING"
        await handle.terminate("test complete")
