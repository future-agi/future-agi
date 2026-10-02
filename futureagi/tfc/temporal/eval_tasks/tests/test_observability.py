"""Observability e2e: Search Attributes + memo applied (and re-applied
across continue-as-new), the ``phase`` query, the ``request_recheck`` nudge
signal, and idempotent SA registration. Uses an in-memory Temporal server with
the eval-task SAs registered on its namespace.

Run sequentially (no xdist):
    set -a && source .env.test.local && set +a
    uv run pytest tfc/temporal/eval_tasks/tests/test_observability.py -p no:xdist -m e2e
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks.registration import register_search_attributes
from tfc.temporal.eval_tasks.search_attributes import (
    ORG_ID,
    PROJECT_ID,
    RUN_TYPE,
    SEARCH_ATTRIBUTE_NAMES,
    TASK_STATUS,
)
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

pytestmark = [pytest.mark.e2e, pytest.mark.xdist_group("temporal_eval_task_e2e")]


def _patch_noop_reconcile(monkeypatch):
    from tracer.services.eval_tasks.reconciler import ReconcileResult

    monkeypatch.setattr(
        "tracer.services.eval_tasks.reconciler.reconcile",
        lambda task: ReconcileResult(),
    )


def _patch_completing_run_entry(monkeypatch):
    def _complete(entry):
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _complete)


@sync_to_async
def _set_status(task_id, status):
    EvalTask.objects.filter(id=task_id).update(status=status)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def registered_sas(workflow_environment):
    """Register the eval-task Search Attributes on the in-memory namespace once."""
    await register_search_attributes(
        workflow_environment.client, workflow_environment.client.namespace
    )
    return workflow_environment


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
class TestSearchAttributeRegistration:
    async def test_registration_is_idempotent_and_lists_keys(self, registered_sas):
        from temporalio.api.operatorservice.v1 import ListSearchAttributesRequest

        client = registered_sas.client
        # Already registered by the fixture → re-registering must not raise,
        # whether the server reports already-exists (real cluster) or silently
        # succeeds (dev server).
        await register_search_attributes(client, client.namespace)
        resp = await client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=client.namespace)
        )
        for name in SEARCH_ATTRIBUTE_NAMES:
            assert name in resp.custom_attributes


@pytest.mark.django_db(transaction=True)
class TestWorkflowLabels:
    async def test_search_attributes_and_memo_applied(
        self, registered_sas, eval_task, make_pending_entries, monkeypatch
    ):
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 2)

        env = registered_sas
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)
        async with _worker(env, queue):
            result = await env.client.execute_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(task_id=task_id, task_queue=queue, batch_size=2),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            assert result.status == "completed"
            desc = await env.client.get_workflow_handle(
                f"eval-task-{task_id}"
            ).describe()

        sas = desc.typed_search_attributes
        assert sas[ORG_ID] == str(eval_task.project.organization_id)
        assert sas[PROJECT_ID] == str(eval_task.project_id)
        assert sas[RUN_TYPE] == "historical"
        assert sas[TASK_STATUS] == EvalTaskStatus.COMPLETED
        memo = await desc.memo()
        assert memo["task_name"] == "WF Task"
        assert memo["project_name"] == "WF Test Project"

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

    async def test_status_sa_reflects_real_status_not_blanket_paused(
        self, registered_sas, eval_task, make_pending_entries, monkeypatch
    ):
        """A task that goes inactive for a reason other than pause (here FAILED)
        must stamp EvalTaskStatus with its real status, not a blanket "paused" —
        else ops filtering the fleet by EvalTaskStatus=failed silently miss it."""
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 2)

        env = registered_sas
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)
        # Task failed before the drain loop's first state check.
        await _set_status(task_id, EvalTaskStatus.FAILED)
        async with _worker(env, queue):
            result = await env.client.execute_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(task_id=task_id, task_queue=queue, batch_size=2),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            # The run's own outcome is "paused" (its inactive-exit label), but the
            # search attribute must carry the real status for fleet filtering.
            assert result.status == "paused"
            desc = await env.client.get_workflow_handle(
                f"eval-task-{task_id}"
            ).describe()

        assert desc.typed_search_attributes[TASK_STATUS] == EvalTaskStatus.FAILED

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()

    async def test_labels_reapplied_after_continue_as_new(
        self, registered_sas, eval_task, make_pending_entries, monkeypatch
    ):
        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 3)

        env = registered_sas
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)
        async with _worker(env, queue):
            await env.client.execute_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=task_id,
                    task_queue=queue,
                    batch_size=1,
                    continue_as_new_after_batches=1,  # force CAN between batches
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            desc = await env.client.get_workflow_handle(
                f"eval-task-{task_id}"
            ).describe()

        # Labels survived the continue-as-new chain (re-applied on the final run).
        assert desc.typed_search_attributes[ORG_ID] == str(
            eval_task.project.organization_id
        )
        assert desc.typed_search_attributes[TASK_STATUS] == EvalTaskStatus.COMPLETED

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()


@pytest.mark.django_db(transaction=True)
class TestPhaseQuery:
    async def test_phase_reports_draining_then_done(
        self, registered_sas, eval_task, make_pending_entries, monkeypatch
    ):
        import threading

        from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
        from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        await sync_to_async(make_pending_entries)(eval_task, 1)

        reached = threading.Event()
        release = threading.Event()

        def _blocking(entry):
            reached.set()
            release.wait(timeout=15)
            EvalLogger.objects.filter(id=entry.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
            return EvalEntryStatus.COMPLETED

        monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", _blocking)

        env = registered_sas
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)
        async with _worker(env, queue):
            handle = await env.client.start_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(task_id=task_id, task_queue=queue, batch_size=1),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            assert await sync_to_async(reached.wait)(10) is True
            assert await handle.query(HistoricalEvalTaskWorkflow.phase) == "draining"
            release.set()
            await handle.result()
            assert await handle.query(HistoricalEvalTaskWorkflow.phase) == "done"

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()


@pytest.mark.django_db(transaction=True)
class TestRecheckSignal:
    async def test_signal_wakes_sleeping_continuous(
        self, registered_sas, eval_task, monkeypatch
    ):
        from tfc.temporal.eval_tasks.types import ContinuousDrainState
        from tfc.temporal.eval_tasks.workflows import ContinuousEvalTaskWorkflow

        _patch_noop_reconcile(monkeypatch)
        _patch_completing_run_entry(monkeypatch)
        # No entries → the loop drains nothing and goes to sleep for 30s.

        env = registered_sas
        queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
        task_id = str(eval_task.id)
        async with _worker(env, queue):
            handle = await env.client.start_workflow(
                ContinuousEvalTaskWorkflow.run,
                ContinuousDrainState(
                    task_id=task_id, task_queue=queue, poll_interval_seconds=30
                ),
                id=f"eval-task-{task_id}",
                task_queue=queue,
            )
            # Wait until it's sleeping, then pause + nudge.
            for _ in range(50):
                if await handle.query(ContinuousEvalTaskWorkflow.phase) == "sleeping":
                    break
                await asyncio.sleep(0.2)
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await handle.signal(ContinuousEvalTaskWorkflow.request_recheck)

            # The nudge wakes the 30s sleep immediately; without it this times out.
            await asyncio.wait_for(handle.result(), timeout=10)

        await asyncio.sleep(0.2)
        await sync_to_async(close_old_connections)()
