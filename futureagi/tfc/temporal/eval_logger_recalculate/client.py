"""Workflow starters for Django callers."""

import uuid
from typing import List, Optional

from tfc.temporal.common.client import (
    start_workflow_async,
    start_workflow_sync,
)
from tfc.temporal.eval_logger_recalculate.types import (
    RecalculateEvalTaskWorkflowInput,
    RecalculateTarget,
)


def _workflow_id(eval_task_id: str) -> str:
    # Per-eval-task prefix + short uuid so retries on the same task get distinct ids.
    return f"recalculate-eval-task-{eval_task_id}-{uuid.uuid4().hex[:8]}"


async def start_recalculate_eval_task_workflow_async(
    eval_task_id: str,
    custom_eval_config_id: str,
    targets: List[RecalculateTarget],
    feedback_id: Optional[str] = None,
    max_concurrent: int = 10,
    task_queue: str = "tasks_s",
) -> str:
    from tfc.temporal.eval_logger_recalculate.workflows import (
        RecalculateEvalTaskWorkflow,
    )

    handle = await start_workflow_async(
        workflow_class=RecalculateEvalTaskWorkflow,
        workflow_input=RecalculateEvalTaskWorkflowInput(
            eval_task_id=eval_task_id,
            custom_eval_config_id=custom_eval_config_id,
            feedback_id=feedback_id,
            targets=targets,
            max_concurrent=max_concurrent,
            task_queue=task_queue,
        ),
        workflow_id=_workflow_id(eval_task_id),
        task_queue=task_queue,
        cancel_existing=False,
    )
    return handle.id


def start_recalculate_eval_task_workflow(
    eval_task_id: str,
    custom_eval_config_id: str,
    targets: List[RecalculateTarget],
    feedback_id: Optional[str] = None,
    max_concurrent: int = 10,
    task_queue: str = "tasks_s",
) -> str:
    from tfc.temporal.eval_logger_recalculate.workflows import (
        RecalculateEvalTaskWorkflow,
    )

    handle = start_workflow_sync(
        workflow_class=RecalculateEvalTaskWorkflow,
        workflow_input=RecalculateEvalTaskWorkflowInput(
            eval_task_id=eval_task_id,
            custom_eval_config_id=custom_eval_config_id,
            feedback_id=feedback_id,
            targets=targets,
            max_concurrent=max_concurrent,
            task_queue=task_queue,
        ),
        workflow_id=_workflow_id(eval_task_id),
        task_queue=task_queue,
        cancel_existing=False,
    )
    return handle.id


__all__ = [
    "start_recalculate_eval_task_workflow",
    "start_recalculate_eval_task_workflow_async",
]
