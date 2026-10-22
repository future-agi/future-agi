"""Workflow starters for eval tasks.

``start_eval_task_workflow`` picks the historical or continuous workflow by the
task's ``run_type`` and starts it under the per-task id ``eval-task-{id}`` so at
most one workflow runs per task. Wired into the views at cutover (PR 9).
"""

import structlog
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from tfc.temporal.common.client import (
    signal_workflow_sync,
    start_workflow_async,
    start_workflow_sync,
)

logger = structlog.get_logger(__name__)


def _workflow_id(task_id: str) -> str:
    return f"eval-task-{task_id}"


def _select(task, task_queue, *, workflow_confirmed_stopped: bool = False):
    """Return (workflow_class, workflow_input) for the task's run_type."""
    from tfc.temporal.eval_tasks.types import (
        ContinuousDrainState,
        EvalTaskWorkflowInput,
    )
    from tfc.temporal.eval_tasks.workflows import (
        ContinuousEvalTaskWorkflow,
        HistoricalEvalTaskWorkflow,
    )
    from tracer.models.eval_task import RunType

    if task.run_type == RunType.CONTINUOUS:
        return ContinuousEvalTaskWorkflow, ContinuousDrainState(
            task_id=str(task.id),
            task_queue=task_queue,
            workflow_confirmed_stopped=workflow_confirmed_stopped,
        )
    return HistoricalEvalTaskWorkflow, EvalTaskWorkflowInput(
        task_id=str(task.id),
        task_queue=task_queue,
        workflow_confirmed_stopped=workflow_confirmed_stopped,
    )


def start_eval_task_workflow_sync(
    task,
    task_queue: str = "tasks_s",
    *,
    replace_existing: bool = False,
    workflow_confirmed_stopped: bool | None = None,
) -> str:
    """Start the workflow for ``task`` from synchronous Django code.

    New-task/legacy callers coalesce with an already-active workflow. A rerun
    or resume passes ``replace_existing=True`` after its PENDING state commits,
    so a stale or closing execution cannot absorb the start and strand the row.

    Every start carries into the workflow input whether a describe of the
    task's workflow id found nothing draining, because the first thing a fresh
    run does is reap: see ``_describe_says_nothing_is_draining``. The starter
    takes that describe itself unless the caller passes the answer of one it
    has just taken — the sweep's restart does, so a restart costs one describe
    and cannot lose the answer to a second call failing. Only a coalescing
    start may bring its own answer. A coalescing start that finds a live
    execution is absorbed by it, input and all, so the answer only ever
    reaches a run that started with nothing draining. A replacing start
    terminates whatever it finds, and whether that was a live drain is exactly
    what the starter's own describe, taken just before, has to say.
    """
    if workflow_confirmed_stopped is None:
        workflow_confirmed_stopped = _describe_says_nothing_is_draining(task.id)
    elif replace_existing:
        raise ValueError(
            "a replacing start must describe the workflow itself; "
            "workflow_confirmed_stopped is for coalescing starts only"
        )
    workflow_class, workflow_input = _select(
        task,
        task_queue,
        workflow_confirmed_stopped=workflow_confirmed_stopped,
    )
    conflict_policy = (
        WorkflowIDConflictPolicy.TERMINATE_EXISTING
        if replace_existing
        else WorkflowIDConflictPolicy.USE_EXISTING
    )
    handle = start_workflow_sync(
        workflow_class=workflow_class,
        workflow_input=workflow_input,
        workflow_id=_workflow_id(str(task.id)),
        task_queue=task_queue,
        cancel_existing=False,
        # Closed executions may be followed by a new run under the per-task ID.
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=conflict_policy,
    )
    return handle.id


# What a describe of the per-task workflow id told us about the task's drain.
WF_PROGRESSING = "progressing"
WF_ABSENT = "absent"
WF_CLOSED = "closed"


async def describe_eval_task_workflow_async(task_id) -> str:
    """Classify the task's workflow: progressing, closed, or never/no longer there.

    ``WF_PROGRESSING`` means a RUNNING execution owns the task and must be left
    alone. ``WF_ABSENT`` (NOT_FOUND — retention expired, or one was never
    started) and ``WF_CLOSED`` (completed / failed / terminated / timed out)
    both mean nothing is draining the task. Any other RPC failure is the
    Temporal service being unreachable, not an answer, so it propagates: the
    caller must skip that task rather than restart it blind.
    """
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    from tfc.temporal.common.client import get_client

    client = await get_client()
    handle = client.get_workflow_handle(_workflow_id(str(task_id)))
    try:
        description = await handle.describe()
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return WF_ABSENT
        raise
    if description.status == WorkflowExecutionStatus.RUNNING:
        return WF_PROGRESSING
    return WF_CLOSED


def describe_eval_task_workflow_sync(task_id) -> str:
    from tfc.temporal.common.client import _run_async_in_sync_context

    return _run_async_in_sync_context(
        lambda: describe_eval_task_workflow_async(task_id)
    )


def _describe_says_nothing_is_draining(task_id) -> bool:
    """Whether the server just said no execution owns this task's workflow id.

    This is the evidence the first act of the run it precedes needs. A fresh
    execution reaps before it claims, asking for ``ReapInput``'s 600 s, and
    ``effective_stale_seconds`` raises that to ninety minutes unless something
    can show the reap is not racing a live dispatcher. Nothing inside the
    workflow can show it — a describe taken from within would find the
    execution asking. It has to be taken here, in the moment between the old
    execution ending and the new one starting, which is where Resume and Edit
    → Save pass. The sweep's restart passes there too, but brings the answer
    of the describe its recovery is gated on rather than asking twice.

    False on any failure, including an unreachable Temporal: no answer is not
    a negative answer, and the floor is the safe reading of silence. That
    choice changes what the run reclaims — ninety minutes instead of ten — and
    the start that follows can still succeed (a describe-only failure, or a
    transient that cleared), so the failure is logged with its traceback here
    rather than left to the start to surface.
    """
    try:
        return describe_eval_task_workflow_sync(task_id) in (WF_ABSENT, WF_CLOSED)
    except Exception as exc:
        _log_describe_fallback(task_id, exc)
        return False


async def _describe_says_nothing_is_draining_async(task_id) -> bool:
    """``_describe_says_nothing_is_draining`` for the async starter."""
    try:
        return (await describe_eval_task_workflow_async(task_id)) in (
            WF_ABSENT,
            WF_CLOSED,
        )
    except Exception as exc:
        _log_describe_fallback(task_id, exc)
        return False


def _log_describe_fallback(task_id, exc: Exception) -> None:
    """Record why a start fell back to the blind reap floor, traceback
    included."""
    logger.warning(
        "eval_task_start_describe_failed",
        task_id=str(task_id),
        fallback="blind_reap_floor",
        error_type=type(exc).__name__,
        exc_info=exc,
    )


def signal_pause_eval_task_workflow(task_id) -> bool:
    """Tell the running workflow to stop launching new evals at once. Best-effort
    — the paused DB status the caller already wrote is the durable source of
    truth the workflow also checks at each batch boundary."""
    return signal_workflow_sync(_workflow_id(str(task_id)), "pause")


async def start_eval_task_workflow_async(
    task,
    task_queue: str = "tasks_s",
    *,
    replace_existing: bool = False,
) -> str:
    workflow_class, workflow_input = _select(
        task,
        task_queue,
        workflow_confirmed_stopped=await _describe_says_nothing_is_draining_async(
            task.id
        ),
    )
    conflict_policy = (
        WorkflowIDConflictPolicy.TERMINATE_EXISTING
        if replace_existing
        else WorkflowIDConflictPolicy.USE_EXISTING
    )
    handle = await start_workflow_async(
        workflow_class=workflow_class,
        workflow_input=workflow_input,
        workflow_id=_workflow_id(str(task.id)),
        task_queue=task_queue,
        cancel_existing=False,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=conflict_policy,
    )
    return handle.id


__all__ = [
    "WF_ABSENT",
    "WF_CLOSED",
    "WF_PROGRESSING",
    "describe_eval_task_workflow_async",
    "describe_eval_task_workflow_sync",
    "start_eval_task_workflow_sync",
    "start_eval_task_workflow_async",
    "signal_pause_eval_task_workflow",
]
