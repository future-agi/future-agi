"""Recover ambiguous experiment starts by durable submission identity."""

import asyncio

from django.db import close_old_connections
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from tfc.temporal.common.client import get_client


def _ensure_targets_admitted_sync(workflow_input):
    from model_hub.services.experiment_target_admission import ensure_v2_run_admitted

    close_old_connections()
    try:
        ensure_v2_run_admitted(
            str(workflow_input.experiment_id),
            rerun_prompt_config_ids=workflow_input.rerun_prompt_config_ids,
            rerun_agent_config_ids=workflow_input.rerun_agent_config_ids,
            rerun_eval_template_ids=workflow_input.rerun_eval_template_ids,
            column_changed=workflow_input.column_changed,
        )
    finally:
        close_old_connections()


async def _ensure_targets_admitted(workflow_input):
    """Validate stored target bindings before a new start, under either flag.

    The ORM read runs on a pool thread (thread_sensitive=False): the sync
    bridge's caller thread is blocked waiting on this coroutine, so a
    thread-sensitive hop back to it would deadlock.
    """
    from tfc.telemetry import otel_sync_to_async

    await otel_sync_to_async(_ensure_targets_admitted_sync, thread_sensitive=False)(
        workflow_input
    )


async def admit_v2_workflow(workflow_input, admission_id, task_queue):
    from tfc.temporal.experiments.workflows import RunExperimentV2Workflow

    async def admit():
        client = await get_client()
        workflow_id = f"experiment-{workflow_input.experiment_id}"
        handle = client.get_workflow_handle(workflow_id)

        async def already_admitted():
            try:
                description = await handle.describe()
            except RPCError as error:
                if error.status == RPCStatusCode.NOT_FOUND:
                    return False
                raise
            memo = await description.memo()
            return memo.get("experiment_submission_id") == admission_id

        if await already_admitted():
            return workflow_id
        # A matching-memo replay above starts nothing; a new start is admission.
        await _ensure_targets_admitted(workflow_input)
        try:
            admitted = await client.start_workflow(
                RunExperimentV2Workflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=task_queue,
                memo={"experiment_submission_id": admission_id},
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
            return admitted.id
        except WorkflowAlreadyStartedError:
            if await already_admitted():
                return workflow_id
            raise

    return await asyncio.wait_for(admit(), timeout=15)
