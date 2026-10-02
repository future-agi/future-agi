"""Per-task eval-task Temporal domain.

Only types + workflows are imported at module level (no Django); activities and
client are lazy-loaded so workflow-sandbox validation never pulls in Django.
"""

from tfc.temporal.eval_tasks.types import (
    ContinuousDrainState,
    EvalTaskWorkflowInput,
    EvalTaskWorkflowOutput,
)
from tfc.temporal.eval_tasks.workflows import (
    ContinuousEvalTaskWorkflow,
    HistoricalEvalTaskWorkflow,
)


def get_workflows():
    return [HistoricalEvalTaskWorkflow, ContinuousEvalTaskWorkflow]


def get_activities():
    from tfc.temporal.eval_tasks.activities import (
        claim_eval_batch_activity,
        fail_eval_entry_activity,
        finalize_eval_task_activity,
        get_eval_task_state_activity,
        get_workflow_labels_activity,
        reap_stale_running_activity,
        reconcile_eval_task_activity,
        requeue_eval_entries_activity,
        run_eval_entry_activity,
        set_eval_task_status_activity,
    )

    return [
        reconcile_eval_task_activity,
        claim_eval_batch_activity,
        run_eval_entry_activity,
        fail_eval_entry_activity,
        reap_stale_running_activity,
        get_eval_task_state_activity,
        get_workflow_labels_activity,
        requeue_eval_entries_activity,
        set_eval_task_status_activity,
        finalize_eval_task_activity,
    ]


def start_eval_task_workflow(*args, **kwargs):
    from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync

    return start_eval_task_workflow_sync(*args, **kwargs)


def start_eval_task_workflow_async(*args, **kwargs):
    from tfc.temporal.eval_tasks.client import start_eval_task_workflow_async as _start

    return _start(*args, **kwargs)


__all__ = [
    "EvalTaskWorkflowInput",
    "EvalTaskWorkflowOutput",
    "ContinuousDrainState",
    "HistoricalEvalTaskWorkflow",
    "ContinuousEvalTaskWorkflow",
    "get_workflows",
    "get_activities",
    "start_eval_task_workflow",
    "start_eval_task_workflow_async",
]
