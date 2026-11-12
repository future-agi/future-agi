"""
Temporal client utilities for experiments.

Domain-specific workflow starters and status checkers.
Uses the centralized client from tfc.temporal.common.
"""

import asyncio

from temporalio.client import WorkflowExecutionStatus

from tfc.temporal.common.client import (
    _run_async_in_sync_context,
    cancel_workflow_async,
    cancel_workflow_sync,
    get_client,
    get_workflow_status_async,
    get_workflow_status_sync,
    start_workflow_async,
    start_workflow_sync,
)

# =============================================================================
# Experiment Workflow Helpers
# =============================================================================


def _get_experiment_workflow_id(experiment_id: str) -> str:
    """Generate workflow ID for an experiment."""
    return f"experiment-{experiment_id}"


async def start_experiment_workflow_async(
    experiment_id: str,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
) -> str:
    """
    Start an experiment workflow asynchronously.

    Args:
        experiment_id: The experiment UUID to run
        max_concurrent_rows: Maximum concurrent row processing per prompt
        task_queue: Temporal task queue to use

    Returns:
        The workflow ID
    """
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentWorkflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

    handle = await start_workflow_async(
        workflow_class=RunExperimentWorkflow,
        workflow_input=RunExperimentInput(
            experiment_id=experiment_id,
            max_concurrent_rows=max_concurrent_rows,
            task_queue=task_queue,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


def start_experiment_workflow(
    experiment_id: str,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
) -> str:
    """
    Start an experiment workflow synchronously.

    Convenience wrapper for Django views.

    Args:
        experiment_id: The experiment UUID to run
        max_concurrent_rows: Maximum concurrent row processing per prompt
        task_queue: Temporal task queue to use

    Returns:
        The workflow ID
    """
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentWorkflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

    handle = start_workflow_sync(
        workflow_class=RunExperimentWorkflow,
        workflow_input=RunExperimentInput(
            experiment_id=experiment_id,
            max_concurrent_rows=max_concurrent_rows,
            task_queue=task_queue,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


def _ensure_v2_run_admitted(
    experiment_id,
    rerun_prompt_config_ids,
    rerun_agent_config_ids,
    rerun_eval_template_ids,
    column_changed,
) -> None:
    """Validate the stored agent targets this run will actually execute."""
    from model_hub.services.experiment_target_admission import ensure_v2_run_admitted

    ensure_v2_run_admitted(
        experiment_id,
        rerun_prompt_config_ids=rerun_prompt_config_ids,
        rerun_agent_config_ids=rerun_agent_config_ids,
        rerun_eval_template_ids=rerun_eval_template_ids,
        column_changed=column_changed,
    )


async def start_experiment_v2_workflow_async(
    experiment_id: str,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
    rerun_prompt_config_ids: list = None,
    rerun_agent_config_ids: list = None,
    rerun_eval_template_ids: list = None,
    column_changed: bool = False,
    admission_id: str | None = None,
    native_runs: dict | None = None,
    http_only: bool = False,
) -> str:
    """
    Start a V2 experiment workflow asynchronously.

    Uses RunExperimentV2Workflow which supports structured EPC/EAC configs
    and dependency-aware eval orchestration.

    Args:
        experiment_id: The experiment UUID to run
        max_concurrent_rows: Maximum concurrent row processing per prompt
        task_queue: Temporal task queue to use
        rerun_prompt_config_ids: If non-empty, only re-run these EPC IDs
        rerun_agent_config_ids: If non-empty, only re-run these EAC IDs
        rerun_eval_template_ids: If non-empty, only re-run these eval IDs
        column_changed: If True, base column changed — re-run base evals only

    Returns:
        The workflow ID
    """
    if native_runs and not admission_id:
        raise ValueError("Native execution requires a committed admission identity")
    from asgiref.sync import sync_to_async

    if http_only:
        from model_hub.services.http_experiment_source import ensure_http_mutation_admitted
        await sync_to_async(ensure_http_mutation_admitted)(experiment_id, admission_id)
    else:
        await sync_to_async(_ensure_v2_run_admitted)(
            experiment_id,
            rerun_prompt_config_ids,
            rerun_agent_config_ids,
            rerun_eval_template_ids,
            column_changed,
        )
    return await _start_admitted_v2_workflow_async(
        experiment_id=experiment_id,
        max_concurrent_rows=max_concurrent_rows,
        task_queue=task_queue,
        rerun_prompt_config_ids=rerun_prompt_config_ids,
        rerun_agent_config_ids=rerun_agent_config_ids,
        rerun_eval_template_ids=rerun_eval_template_ids,
        column_changed=column_changed,
        admission_id=admission_id,
        native_runs=native_runs, http_only=http_only,
    )


async def _start_admitted_v2_workflow_async(
    experiment_id: str,
    max_concurrent_rows: int,
    task_queue: str,
    rerun_prompt_config_ids: list,
    rerun_agent_config_ids: list,
    rerun_eval_template_ids: list,
    column_changed: bool,
    admission_id: str | None,
    native_runs: dict | None = None,
    http_only: bool = False,
) -> str:
    """Start a V2 run whose agent targets were already admitted."""
    if native_runs and not admission_id:
        raise ValueError("Native execution requires a committed admission identity")
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentV2Workflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

    if admission_id:
        from tfc.temporal.experiments.admission import admit_v2_workflow

        admitted_id = await admit_v2_workflow(
            RunExperimentInput(
                experiment_id=experiment_id,
                max_concurrent_rows=max_concurrent_rows,
                task_queue=task_queue,
                rerun_prompt_config_ids=rerun_prompt_config_ids or [],
                rerun_agent_config_ids=rerun_agent_config_ids or [],
                rerun_eval_template_ids=rerun_eval_template_ids or [],
                column_changed=column_changed,
                native_runs=native_runs or {},
            ),
            admission_id,
            task_queue,
            http_admission_id=admission_id if http_only else None,
        )
        if native_runs or http_only:
            # The shared memo protocol also recognizes closed historical runs.
            # A pending native run cannot recover from a dead worker: it has not
            # been queued yet, so even COMPLETED is not a valid acknowledgement.
            async def confirm_live():
                remote = await get_client()
                description = await remote.get_workflow_handle(admitted_id).describe()
                memo = await description.memo()
                if (memo.get("experiment_submission_id") != admission_id
                        or description.status != WorkflowExecutionStatus.RUNNING):
                    raise ValueError("Native admission has no live matching workflow")

            await asyncio.wait_for(confirm_live(), timeout=15)
        return admitted_id

    handle = await start_workflow_async(
        workflow_class=RunExperimentV2Workflow,
        workflow_input=RunExperimentInput(
            experiment_id=experiment_id,
            max_concurrent_rows=max_concurrent_rows,
            task_queue=task_queue,
            rerun_prompt_config_ids=rerun_prompt_config_ids or [],
            rerun_agent_config_ids=rerun_agent_config_ids or [],
            rerun_eval_template_ids=rerun_eval_template_ids or [],
            column_changed=column_changed,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


def start_experiment_v2_workflow(
    experiment_id: str,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
    rerun_prompt_config_ids: list = None,
    rerun_agent_config_ids: list = None,
    rerun_eval_template_ids: list = None,
    column_changed: bool = False,
    admission_id: str | None = None,
    native_runs: dict | None = None,
    http_only: bool = False,
) -> str:
    """
    Start a V2 experiment workflow synchronously.

    Convenience wrapper for Django views. Uses RunExperimentV2Workflow
    which supports structured EPC/EAC configs.

    Args:
        experiment_id: The experiment UUID to run
        max_concurrent_rows: Maximum concurrent row processing per prompt
        task_queue: Temporal task queue to use
        rerun_prompt_config_ids: If non-empty, only re-run these EPC IDs
        rerun_agent_config_ids: If non-empty, only re-run these EAC IDs
        rerun_eval_template_ids: If non-empty, only re-run these eval IDs
        column_changed: If True, base column changed — re-run base evals only

    Returns:
        The workflow ID
    """
    if native_runs and not admission_id:
        raise ValueError("Native execution requires a committed admission identity")
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentV2Workflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

    if http_only:
        from model_hub.services.http_experiment_source import ensure_http_mutation_admitted
        ensure_http_mutation_admitted(experiment_id, admission_id)
    else:
        _ensure_v2_run_admitted(
            experiment_id,
            rerun_prompt_config_ids,
            rerun_agent_config_ids,
            rerun_eval_template_ids,
            column_changed,
        )
    if admission_id:
        return _run_async_in_sync_context(
            lambda: _start_admitted_v2_workflow_async(
                experiment_id=experiment_id,
                max_concurrent_rows=max_concurrent_rows,
                task_queue=task_queue,
                rerun_prompt_config_ids=rerun_prompt_config_ids,
                rerun_agent_config_ids=rerun_agent_config_ids,
                rerun_eval_template_ids=rerun_eval_template_ids,
                column_changed=column_changed,
                admission_id=admission_id,
                native_runs=native_runs, http_only=http_only,
            )
        )

    handle = start_workflow_sync(
        workflow_class=RunExperimentV2Workflow,
        workflow_input=RunExperimentInput(
            experiment_id=experiment_id,
            max_concurrent_rows=max_concurrent_rows,
            task_queue=task_queue,
            rerun_prompt_config_ids=rerun_prompt_config_ids or [],
            rerun_agent_config_ids=rerun_agent_config_ids or [],
            rerun_eval_template_ids=rerun_eval_template_ids or [],
            column_changed=column_changed,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


async def get_experiment_workflow_status_async(experiment_id: str) -> dict | None:
    """
    Get the status of an experiment workflow.

    Args:
        experiment_id: The experiment UUID

    Returns:
        Dict with workflow status info, or None if not found
    """
    workflow_id = _get_experiment_workflow_id(experiment_id)
    return await get_workflow_status_async(workflow_id)


def get_experiment_workflow_status(experiment_id: str) -> dict | None:
    """
    Get experiment workflow status synchronously.

    Args:
        experiment_id: The experiment UUID

    Returns:
        Dict with workflow status info, or None if not found
    """
    workflow_id = _get_experiment_workflow_id(experiment_id)
    return get_workflow_status_sync(workflow_id)


async def cancel_experiment_workflow_async(experiment_id: str) -> bool:
    """
    Cancel a running experiment workflow.

    Args:
        experiment_id: The experiment UUID

    Returns:
        True if cancellation was requested, False if workflow not found
    """
    workflow_id = _get_experiment_workflow_id(experiment_id)
    return await cancel_workflow_async(workflow_id)


def cancel_experiment_workflow(experiment_id: str) -> bool:
    """
    Cancel experiment workflow synchronously.

    Args:
        experiment_id: The experiment UUID

    Returns:
        True if cancellation was requested, False if workflow not found
    """
    workflow_id = _get_experiment_workflow_id(experiment_id)
    return cancel_workflow_sync(workflow_id)


async def cancel_all_experiment_workflows_async(experiment_id: str) -> dict:
    """
    Cancel ALL running workflows for an experiment.

    Cancels:
    1. Main workflow: experiment-{experiment_id}
    2. All rerun workflows: rerun-experiment-cells-{experiment_id}-*

    DB cleanup (marking cells as ERROR, experiment as CANCELLED) is handled by
    each workflow's CancelledError handler in a detached cancellation scope.

    Args:
        experiment_id: The experiment UUID

    Returns:
        Dict with cancel results: {"main_cancelled": bool, "rerun_cancelled": int}
    """
    client = await get_client()
    result = {"main_cancelled": False, "rerun_cancelled": 0}

    # 1. Cancel main workflow
    main_workflow_id = _get_experiment_workflow_id(experiment_id)
    try:
        handle = client.get_workflow_handle(main_workflow_id)
        desc = await handle.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            await handle.cancel()
            result["main_cancelled"] = True
    except Exception:
        pass  # Workflow doesn't exist or already completed

    # 2. Find and cancel all rerun workflows via visibility query
    rerun_prefix = f"rerun-experiment-cells-{experiment_id}-"
    query = f"WorkflowId STARTS_WITH \"{rerun_prefix}\" AND ExecutionStatus = 'Running'"
    try:
        async for wf_info in client.list_workflows(query=query):
            try:
                handle = client.get_workflow_handle(wf_info.id)
                await handle.cancel()
                result["rerun_cancelled"] += 1
            except Exception:
                pass  # Best effort - continue cancelling others
    except Exception:
        pass  # list_workflows may fail if visibility store is unavailable

    return result


def cancel_all_experiment_workflows(experiment_id: str) -> dict:
    """Cancel all experiment workflows synchronously."""
    return _run_async_in_sync_context(
        lambda: cancel_all_experiment_workflows_async(experiment_id)
    )


async def _admit_subset_workflow_async(
    workflow_input, admission_id: str, task_queue: str,
) -> str:
    """Start or recover the one parent workflow of a committed subset plan.

    The workflow ID and memo are the plan identity. A retry after a lost start
    acknowledgement returns the exact live workflow and never starts another.
    A terminal or foreign workflow cannot acknowledge a pending native plan.
    """
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from temporalio.service import RPCError, RPCStatusCode

    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    contexts = workflow_input.native_runs
    plan_ids = {context.get("plan_id") for context in contexts.values()}
    plan_hashes = {context.get("plan_hash") for context in contexts.values()}
    if (not contexts or plan_ids != {admission_id} or len(plan_hashes) != 1
            or None in plan_hashes):
        raise ValueError("Native subset execution requires one committed plan identity")
    plan_hash = next(iter(plan_hashes))
    workflow_id = f"rerun-experiment-cells-{workflow_input.experiment_id}-{admission_id}"
    memo = {"experiment_submission_id": admission_id, "execution_plan_hash": plan_hash}

    async def live_match():
        remote = await get_client()
        try:
            description = await remote.get_workflow_handle(workflow_id).describe()
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        existing = await description.memo()
        if (existing.get("experiment_submission_id") != admission_id
                or existing.get("execution_plan_hash") != plan_hash
                or description.status != WorkflowExecutionStatus.RUNNING):
            raise ValueError("Native subset admission has no live matching workflow")
        return workflow_id

    async def admit():
        if await live_match():
            return workflow_id
        remote = await get_client()
        try:
            await remote.start_workflow(
                RerunCellsV2Workflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=task_queue,
                memo=memo,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
        except WorkflowAlreadyStartedError:
            pass
        # The parent waits for the committed acknowledgement, so a valid start
        # is live; closed history is never an acknowledgement for a pending plan.
        if await live_match() is None:
            raise ValueError("Native subset admission has no live matching workflow")
        return workflow_id

    return await asyncio.wait_for(admit(), timeout=15)


async def start_rerun_cells_v2_workflow_async(
    experiment_id: str,
    dataset_id: str,
    prompt_config_ids: list = None,
    agent_config_ids: list = None,
    row_ids: list = None,
    failed_only: bool = False,
    eval_template_ids: list = None,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
    eval_only: bool = False,
    edt_ids: list = None,
    base_eval_only: bool = False,
    admission_id: str | None = None,
    native_runs: dict | None = None,
) -> str:
    """Start a cell/column-level rerun workflow asynchronously.

    Uses a unique workflow ID per invocation to allow concurrent reruns. A
    native plan instead uses its committed admission ID (see
    _admit_subset_workflow_async).
    """
    if native_runs and not admission_id:
        raise ValueError("Native execution requires a committed admission identity")
    import uuid as _uuid

    from model_hub.services.experiment_target_admission import (
        ensure_agent_configs_admitted,
    )
    from tfc.telemetry import otel_sync_to_async
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    # The target check now reads ORM bindings, so do not run it on the event loop.

    await otel_sync_to_async(ensure_agent_configs_admitted, thread_sensitive=False)(
        agent_config_ids,
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        row_ids=row_ids,
        eval_only=eval_only,
    )

    workflow_input = RerunCellsV2WorkflowInput(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        prompt_config_ids=prompt_config_ids or [],
        agent_config_ids=agent_config_ids or [],
        row_ids=row_ids or [],
        failed_only=failed_only,
        eval_template_ids=eval_template_ids or [],
        max_concurrent_rows=max_concurrent_rows,
        task_queue=task_queue,
        eval_only=eval_only,
        edt_ids=edt_ids or [],
        base_eval_only=base_eval_only,
        native_runs=native_runs or {},
    )
    if native_runs:
        return await _admit_subset_workflow_async(workflow_input, admission_id, task_queue)

    workflow_id = f"rerun-experiment-cells-{experiment_id}-{_uuid.uuid4()}"

    handle = await start_workflow_async(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=workflow_input,
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


def start_rerun_cells_v2_workflow(
    experiment_id: str,
    dataset_id: str,
    prompt_config_ids: list = None,
    agent_config_ids: list = None,
    row_ids: list = None,
    failed_only: bool = False,
    eval_template_ids: list = None,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
    eval_only: bool = False,
    edt_ids: list = None,
    base_eval_only: bool = False,
    admission_id: str | None = None,
    native_runs: dict | None = None,
) -> str:
    """Start a cell/column-level rerun workflow synchronously.

    Uses a unique workflow ID per invocation to allow concurrent reruns. A
    native plan instead uses its committed admission ID (see
    _admit_subset_workflow_async).
    """
    if native_runs and not admission_id:
        raise ValueError("Native execution requires a committed admission identity")
    import uuid as _uuid

    from model_hub.services.experiment_target_admission import (
        ensure_agent_configs_admitted,
    )
    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    # Eval-only reruns start no agent child and stay admitted.
    ensure_agent_configs_admitted(
        agent_config_ids,
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        row_ids=row_ids,
        eval_only=eval_only,
    )

    workflow_input = RerunCellsV2WorkflowInput(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        prompt_config_ids=prompt_config_ids or [],
        agent_config_ids=agent_config_ids or [],
        row_ids=row_ids or [],
        failed_only=failed_only,
        eval_template_ids=eval_template_ids or [],
        max_concurrent_rows=max_concurrent_rows,
        task_queue=task_queue,
        eval_only=eval_only,
        edt_ids=edt_ids or [],
        base_eval_only=base_eval_only,
        native_runs=native_runs or {},
    )
    if native_runs:
        return _run_async_in_sync_context(
            lambda: _admit_subset_workflow_async(workflow_input, admission_id, task_queue)
        )

    workflow_id = f"rerun-experiment-cells-{experiment_id}-{_uuid.uuid4()}"

    handle = start_workflow_sync(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=workflow_input,
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


__all__ = [
    "start_experiment_workflow",
    "start_experiment_workflow_async",
    "start_experiment_v2_workflow",
    "start_experiment_v2_workflow_async",
    "start_rerun_cells_v2_workflow",
    "start_rerun_cells_v2_workflow_async",
    "get_experiment_workflow_status",
    "get_experiment_workflow_status_async",
    "cancel_experiment_workflow",
    "cancel_experiment_workflow_async",
    "cancel_all_experiment_workflows",
    "cancel_all_experiment_workflows_async",
]
