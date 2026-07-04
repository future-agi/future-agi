"""Per-task eval workflows.

One workflow per task replaces the old global 60s cron: ``HistoricalEvalTask``
reconciles once then drains every pending entry to completion;
``ContinuousEvalTask`` loops — reconcile-forward, drain, durable-sleep — forever
via continue-as-new. Both bound in-flight work with a per-task semaphore and
honour a pause flipped on the task row between batches.

Observability: each workflow upserts Search Attributes (org/project/
run_type/status) + memo at the start of every run (so labels survive
continue-as-new), exposes a ``phase`` query, and accepts a ``request_recheck``
signal that nudges the loop to re-check sooner (the DB stays the source of
truth).

IMPORTANT: no Django imports and no ``workflow.logger`` here — workflows run in
the Temporal sandbox; all DB work and logging live in the activities.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from tfc.temporal.eval_tasks.search_attributes import (
    ORG_ID,
    PHASE_DONE,
    PHASE_DRAINING,
    PHASE_MATERIALIZING,
    PHASE_SLEEPING,
    PHASE_STARTING,
    PROJECT_ID,
    RUN_TYPE,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TASK_STATUS,
)
from tfc.temporal.eval_tasks.types import (
    WF_STATUS_COMPLETED,
    WF_STATUS_PAUSED,
    ClaimBatchInput,
    ContinuousDrainState,
    EvalTaskWorkflowInput,
    EvalTaskWorkflowOutput,
    FinalizeInput,
    ReapInput,
    ReconcileActivityInput,
    RunEntryInput,
    TaskStateInput,
    WorkflowLabelsInput,
)

# Control activities are quick PG reads/writes; run_entry runs the eval engine.
CONTROL_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    backoff_coefficient=2.0,
)
RUN_ENTRY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

_CONTROL_TIMEOUT = timedelta(minutes=30)
_RUN_ENTRY_TIMEOUT = timedelta(hours=12)
_HEARTBEAT = timedelta(minutes=5)


async def _apply_labels(task_id: str) -> None:
    """Upsert the workflow's Search Attributes + memo from the task's DB row.

    Called at the start of every run so labels are re-applied after each
    continue-as-new (SA/memo values are run-scoped).
    """
    labels = await workflow.execute_activity(
        "get_workflow_labels_activity",
        WorkflowLabelsInput(task_id=task_id),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        retry_policy=CONTROL_RETRY_POLICY,
    )
    workflow.upsert_search_attributes(
        [
            ORG_ID.value_set(labels["org_id"]),
            PROJECT_ID.value_set(labels["project_id"]),
            RUN_TYPE.value_set(labels["run_type"]),
        ]
    )
    workflow.upsert_memo(
        {
            "task_name": labels["task_name"],
            "project_name": labels["project_name"],
            "org_name": labels["org_name"],
            "config_summary": labels["config_summary"],
        }
    )


def _set_status(status: str) -> None:
    workflow.upsert_search_attributes([TASK_STATUS.value_set(status)])


async def _reconcile(task_id: str) -> None:
    await workflow.execute_activity(
        "reconcile_eval_task_activity",
        ReconcileActivityInput(task_id=task_id),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        heartbeat_timeout=_HEARTBEAT,
        retry_policy=CONTROL_RETRY_POLICY,
    )


async def _reap(task_id: str) -> None:
    await workflow.execute_activity(
        "reap_stale_running_activity",
        ReapInput(task_id=task_id),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        heartbeat_timeout=_HEARTBEAT,
        retry_policy=CONTROL_RETRY_POLICY,
    )


async def _task_state(task_id: str) -> dict:
    return await workflow.execute_activity(
        "get_eval_task_state_activity",
        TaskStateInput(task_id=task_id),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        retry_policy=CONTROL_RETRY_POLICY,
    )


async def _claim(task_id: str, n: int) -> dict:
    return await workflow.execute_activity(
        "claim_eval_batch_activity",
        ClaimBatchInput(task_id=task_id, n=n),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        retry_policy=CONTROL_RETRY_POLICY,
    )


async def _finalize(task_id: str) -> bool:
    result = await workflow.execute_activity(
        "finalize_eval_task_activity",
        FinalizeInput(task_id=task_id),
        start_to_close_timeout=_CONTROL_TIMEOUT,
        retry_policy=CONTROL_RETRY_POLICY,
    )
    return result["finalized"]


async def _drain_batch(entry_ids: list[str], max_concurrent: int) -> None:
    """Run a claimed batch, capping concurrent eval activities at the per-task
    bound. run_entry self-converges to a terminal state, so retries
    here only cover worker/DB infra blips.

    Per-item isolation: an activity that exhausts its retries (a persistent infra
    fault) is caught and its entry marked errored, so one bad item neither leaves
    work stranded as ``running`` nor fails the whole drain. ``return_exceptions``
    is the backstop for the rare case the fail activity itself can't run.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(entry_id: str) -> None:
        async with semaphore:
            try:
                await workflow.execute_activity(
                    "run_eval_entry_activity",
                    RunEntryInput(entry_id=entry_id),
                    start_to_close_timeout=_RUN_ENTRY_TIMEOUT,
                    heartbeat_timeout=_HEARTBEAT,
                    retry_policy=RUN_ENTRY_RETRY_POLICY,
                )
            except Exception:
                await workflow.execute_activity(
                    "fail_eval_entry_activity",
                    RunEntryInput(entry_id=entry_id),
                    start_to_close_timeout=_CONTROL_TIMEOUT,
                    retry_policy=CONTROL_RETRY_POLICY,
                )

    await asyncio.gather(*[_run_one(eid) for eid in entry_ids], return_exceptions=True)


def _should_continue_as_new(batches: int, threshold) -> bool:
    if workflow.info().is_continue_as_new_suggested():
        return True
    return threshold is not None and batches >= threshold


class _ObservableEvalWorkflow:
    """Shared observability surface for the eval-task workflows: a ``phase``
    query and a ``request_recheck`` pause/edit nudge signal."""

    def __init__(self) -> None:
        self._phase = PHASE_STARTING
        self._recheck = False

    @workflow.query
    def phase(self) -> str:
        return self._phase

    @workflow.signal
    def request_recheck(self) -> None:
        # The DB is the source of truth; this only wakes the loop to
        # re-check pause/edit sooner than its next poll boundary.
        self._recheck = True

    async def _sleep_or_recheck(self, seconds: int) -> None:
        try:
            await workflow.wait_condition(
                lambda: self._recheck, timeout=timedelta(seconds=seconds)
            )
        except TimeoutError:
            pass
        self._recheck = False


@workflow.defn
class HistoricalEvalTaskWorkflow(_ObservableEvalWorkflow):
    """Materialize the desired row set once, then drain it to completion."""

    @workflow.run
    async def run(self, input: EvalTaskWorkflowInput) -> EvalTaskWorkflowOutput:
        self._phase = PHASE_MATERIALIZING
        await _apply_labels(input.task_id)
        _set_status(STATUS_RUNNING)
        if not input.already_reconciled:
            await _reconcile(input.task_id)
            # Reclaim entries left RUNNING by a crashed prior execution.
            await _reap(input.task_id)

        self._phase = PHASE_DRAINING
        processed = input.processed
        batches = 0
        while True:
            state = await _task_state(input.task_id)
            if not state["active"]:
                # Stamp the real terminal status (paused / failed / deleted),
                # not a blanket "paused" — the fleet is filtered by it.
                _set_status(state["status"])
                self._phase = PHASE_DONE
                return EvalTaskWorkflowOutput(
                    task_id=input.task_id,
                    status=WF_STATUS_PAUSED,
                    processed=processed,
                )

            batch = await _claim(input.task_id, input.batch_size)
            entry_ids = batch["entry_ids"]
            if not entry_ids:
                break

            await _drain_batch(entry_ids, input.max_concurrent)
            processed += len(entry_ids)
            batches += 1
            self._recheck = False

            if _should_continue_as_new(batches, input.continue_as_new_after_batches):
                workflow.continue_as_new(
                    EvalTaskWorkflowInput(
                        task_id=input.task_id,
                        task_queue=input.task_queue,
                        batch_size=input.batch_size,
                        max_concurrent=input.max_concurrent,
                        already_reconciled=True,
                        processed=processed,
                        continue_as_new_after_batches=input.continue_as_new_after_batches,
                    )
                )

        if not await _finalize(input.task_id):
            # The drain loop only ends on an empty *pending* claim, so a task
            # that still won't finalize has entries stranded RUNNING — both
            # run_entry and fail_eval_entry exhausted their retries. reap only
            # runs at first start (skipped across continue-as-new), so these
            # can't self-heal here; fail loudly rather than report COMPLETED
            # over undrained work. A fresh workflow start reaps and re-drains.
            _set_status(STATUS_FAILED)
            raise ApplicationError(
                f"eval task {input.task_id} drained but did not finalize",
                non_retryable=True,
            )
        _set_status(STATUS_COMPLETED)
        self._phase = PHASE_DONE
        return EvalTaskWorkflowOutput(
            task_id=input.task_id, status=WF_STATUS_COMPLETED, processed=processed
        )


@workflow.defn
class ContinuousEvalTaskWorkflow(_ObservableEvalWorkflow):
    """Loop forever: reconcile-forward, drain, durable-sleep — never finalizes."""

    @workflow.run
    async def run(self, state: ContinuousDrainState) -> None:
        self._phase = PHASE_MATERIALIZING
        await _apply_labels(state.task_id)
        _set_status(STATUS_RUNNING)
        await _reconcile(state.task_id)
        await _reap(state.task_id)

        while True:
            tstate = await _task_state(state.task_id)
            if not tstate["active"]:
                # Stamp the real terminal status (paused / failed / deleted),
                # not a blanket "paused" — the fleet is filtered by it.
                _set_status(tstate["status"])
                self._phase = PHASE_DONE
                return

            batch = await _claim(state.task_id, state.batch_size)
            entry_ids = batch["entry_ids"]
            if entry_ids:
                self._phase = PHASE_DRAINING
                await _drain_batch(entry_ids, state.max_concurrent)
                state.processed += len(entry_ids)
                state.batches += 1
            else:
                self._phase = PHASE_SLEEPING
                await self._sleep_or_recheck(state.poll_interval_seconds)
                self._phase = PHASE_MATERIALIZING
                await _reconcile(state.task_id)

            if _should_continue_as_new(
                state.batches, state.continue_as_new_after_batches
            ):
                # Reset the per-run batch counter; keep lifetime ``processed``.
                workflow.continue_as_new(
                    ContinuousDrainState(
                        task_id=state.task_id,
                        task_queue=state.task_queue,
                        batch_size=state.batch_size,
                        max_concurrent=state.max_concurrent,
                        poll_interval_seconds=state.poll_interval_seconds,
                        processed=state.processed,
                        batches=0,
                        continue_as_new_after_batches=state.continue_as_new_after_batches,
                    )
                )


__all__ = [
    "HistoricalEvalTaskWorkflow",
    "ContinuousEvalTaskWorkflow",
]
