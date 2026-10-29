"""
Temporal client utilities for experiments.

Domain-specific workflow starters and status checkers.
Uses the centralized client from tfc.temporal.common.
"""

from typing import Optional

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


async def start_experiment_v2_workflow_async(
    experiment_id: str,
    max_concurrent_rows: int = 10,
    task_queue: str = "tasks_l",
    rerun_prompt_config_ids: list = None,
    rerun_agent_config_ids: list = None,
    rerun_eval_template_ids: list = None,
    column_changed: bool = False,
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
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentV2Workflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

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
    from tfc.temporal.experiments.workflows import (
        RunExperimentInput,
        RunExperimentV2Workflow,
    )

    workflow_id = _get_experiment_workflow_id(experiment_id)

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


async def get_experiment_workflow_status_async(experiment_id: str) -> Optional[dict]:
    """
    Get the status of an experiment workflow.

    Args:
        experiment_id: The experiment UUID

    Returns:
        Dict with workflow status info, or None if not found
    """
    workflow_id = _get_experiment_workflow_id(experiment_id)
    return await get_workflow_status_async(workflow_id)


def get_experiment_workflow_status(experiment_id: str) -> Optional[dict]:
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
    query = (
        f'WorkflowId STARTS_WITH "{rerun_prefix}" ' f"AND ExecutionStatus = 'Running'"
    )
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
) -> str:
    """Start a cell/column-level rerun workflow asynchronously.

    Uses a unique workflow ID per invocation to allow concurrent reruns.
    """
    import uuid as _uuid

    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    workflow_id = f"rerun-experiment-cells-{experiment_id}-{_uuid.uuid4()}"

    handle = await start_workflow_async(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=RerunCellsV2WorkflowInput(
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
        ),
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
) -> str:
    """Start a cell/column-level rerun workflow synchronously.

    Uses a unique workflow ID per invocation to allow concurrent reruns.
    """
    import uuid as _uuid

    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    workflow_id = f"rerun-experiment-cells-{experiment_id}-{_uuid.uuid4()}"

    handle = start_workflow_sync(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=RerunCellsV2WorkflowInput(
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
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
    )

    return handle.id


async def start_experiment_eval_rerun_workflow_async(
    experiment_id: str,
    dataset_id: str,
    eval_template_ids: list,
    task_queue: str = "tasks_l",
    max_concurrent_rows: int = 10,
) -> str:
    """Queue an eval-only rerun without cancelling output-producing runs."""
    import uuid as _uuid

    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    workflow_id = f"rerun-experiment-cells-{experiment_id}-evals-{_uuid.uuid4()}"
    handle = await start_workflow_async(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=RerunCellsV2WorkflowInput(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            eval_template_ids=eval_template_ids,
            row_ids=[],
            edt_ids=[],
            eval_only=True,
            wait_for_inflight_runs=True,
            task_queue=task_queue,
            max_concurrent_rows=max_concurrent_rows,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
        cancel_existing=False,
    )
    return handle.id


def start_experiment_eval_rerun_workflow(
    experiment_id: str,
    dataset_id: str,
    eval_template_ids: list,
    task_queue: str = "tasks_l",
    max_concurrent_rows: int = 10,
) -> str:
    """Queue an eval-only rerun synchronously for Django views."""
    import uuid as _uuid

    from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput
    from tfc.temporal.experiments.workflows import RerunCellsV2Workflow

    workflow_id = f"rerun-experiment-cells-{experiment_id}-evals-{_uuid.uuid4()}"
    handle = start_workflow_sync(
        workflow_class=RerunCellsV2Workflow,
        workflow_input=RerunCellsV2WorkflowInput(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            eval_template_ids=eval_template_ids,
            row_ids=[],
            edt_ids=[],
            eval_only=True,
            wait_for_inflight_runs=True,
            task_queue=task_queue,
            max_concurrent_rows=max_concurrent_rows,
        ),
        workflow_id=workflow_id,
        task_queue=task_queue,
        cancel_existing=False,
    )
    return handle.id


__all__ = [
    "start_experiment_workflow",
    "start_experiment_workflow_async",
    "start_experiment_v2_workflow",
    "start_experiment_v2_workflow_async",
    "start_rerun_cells_v2_workflow",
    "start_rerun_cells_v2_workflow_async",
    "start_experiment_eval_rerun_workflow",
    "start_experiment_eval_rerun_workflow_async",
    "get_experiment_workflow_status",
    "get_experiment_workflow_status_async",
    "cancel_experiment_workflow",
    "cancel_experiment_workflow_async",
    "cancel_all_experiment_workflows",
    "cancel_all_experiment_workflows_async",
]
