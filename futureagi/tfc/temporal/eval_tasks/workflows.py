"""Per-task eval workflows.

One workflow per task replaces the old global 60s cron: ``HistoricalEvalTask``
reconciles once then drains every pending entry to completion;
``ContinuousEvalTask`` loops — reconcile-forward, drain, durable-sleep — forever
via continue-as-new. Both bound in-flight work with a per-task semaphore (§9
Layer 2) and honour a pause flipped on the task row between batches (§8).

IMPORTANT: no Django imports and no ``workflow.logger`` here — workflows run in
the Temporal sandbox; all DB work and logging live in the activities.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

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
    bound (§9 Layer 2). run_entry self-converges to a terminal state, so retries
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


@workflow.defn
class HistoricalEvalTaskWorkflow:
    """Materialize the desired row set once, then drain it to completion."""

    @workflow.run
    async def run(self, input: EvalTaskWorkflowInput) -> EvalTaskWorkflowOutput:
        if not input.already_reconciled:
            await _reconcile(input.task_id)
            # Reclaim entries left RUNNING by a crashed prior execution.
            await _reap(input.task_id)

        processed = input.processed
        batches = 0
        while True:
            state = await _task_state(input.task_id)
            if not state["active"]:
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
            raise ApplicationError(
                f"eval task {input.task_id} drained but did not finalize",
                non_retryable=True,
            )
        return EvalTaskWorkflowOutput(
            task_id=input.task_id, status=WF_STATUS_COMPLETED, processed=processed
        )


@workflow.defn
class ContinuousEvalTaskWorkflow:
    """Loop forever: reconcile-forward, drain, durable-sleep — never finalizes."""

    @workflow.run
    async def run(self, state: ContinuousDrainState) -> None:
        await _reconcile(state.task_id)
        await _reap(state.task_id)

        while True:
            tstate = await _task_state(state.task_id)
            if not tstate["active"]:
                return

            batch = await _claim(state.task_id, state.batch_size)
            entry_ids = batch["entry_ids"]
            if entry_ids:
                await _drain_batch(entry_ids, state.max_concurrent)
                state.processed += len(entry_ids)
                state.batches += 1
            else:
                await workflow.sleep(timedelta(seconds=state.poll_interval_seconds))
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
