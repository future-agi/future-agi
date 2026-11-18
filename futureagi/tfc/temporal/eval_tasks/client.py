"""Workflow starters for eval tasks.

``start_eval_task_workflow`` picks the historical or continuous workflow by the
task's ``run_type`` and starts it under the per-task id ``eval-task-{id}`` so at
most one workflow runs per task. Wired into the views at cutover (PR 9).
"""

import asyncio

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


def _select(
    task,
    task_queue,
    *,
    workflow_confirmed_stopped: bool = False,
    gated: bool = False,
    resume_token: str | None = None,
):
    """Return (workflow_class, workflow_input) for the task's run_type."""
    from tfc.temporal.eval_tasks.types import (
        ContinuousDrainState,
        EvalTaskWorkflowInput,
        RequestedContinuousInput,
        RequestedHistoricalInput,
    )
    from tfc.temporal.eval_tasks.workflows import (
        ContinuousEvalTaskWorkflow,
        HistoricalEvalTaskWorkflow,
        RequestedContinuousEvalTaskWorkflow,
        RequestedHistoricalEvalTaskWorkflow,
    )
    from tracer.models.eval_task import RunType

    args = dict(
        task_id=str(task.id),
        task_queue=task_queue,
        workflow_confirmed_stopped=workflow_confirmed_stopped,
    )
    if task.run_type == RunType.CONTINUOUS:
        state = ContinuousDrainState(**args)
        if gated:
            return RequestedContinuousEvalTaskWorkflow, RequestedContinuousInput(
                state, resume_token
            )
        return ContinuousEvalTaskWorkflow, state
    data = EvalTaskWorkflowInput(**args, **_instant_sizing(task))
    if gated:
        return RequestedHistoricalEvalTaskWorkflow, RequestedHistoricalInput(
            data, resume_token
        )
    return HistoricalEvalTaskWorkflow, data


# Instant eval: a person is watching the answers land, so a run drains in
# batches of up to 200 with 32 evals in flight instead of waves of 10 behind a
# 50-row barrier, whatever its size. Each worker process still holds at most
# JEV_MAX_CONCURRENT Jev calls at once. Regular historical tasks keep the
# conservative defaults.
INSTANT_BATCH_SIZE = 200
INSTANT_MAX_CONCURRENT = 32
# A worker that dies mid-answer is noticed after a minute, not five.
INSTANT_HEARTBEAT_S = 60


def _instant_sizing(task) -> dict:
    limit = getattr(task, "spans_limit", None) or 0
    if limit <= 0:
        return {}
    try:
        # Primary: the workflow starts right after the request that set the
        # task's evals; a replica that has not applied them sizes the run as
        # a regular task.
        configs = list(task.evals.using("default").values_list("config", flat=True))
    except Exception:
        # Falls back to the regular sizing; say so, a run sized wrong is slow.
        logger.warning(
            "instant_eval_sizing_unreadable", task_id=str(task.id), exc_info=True
        )
        return {}
    if not configs or not all(
        isinstance(c, dict) and c.get("instant_eval") for c in configs
    ):
        return {}
    sizing = {
        "batch_size": min(limit, INSTANT_BATCH_SIZE),
        "max_concurrent": min(limit, INSTANT_MAX_CONCURRENT),
        "heartbeat_s": INSTANT_HEARTBEAT_S,
    }
    logger.info("instant_eval_sizing", task_id=str(task.id), **sizing)
    return sizing


def start_eval_task_workflow_sync(
    task,
    task_queue: str = "tasks_s",
    *,
    replace_existing: bool = False,
    workflow_confirmed_stopped: bool | None = None,
    start_once: bool = False,
    resume_token: str | None = None,
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

    ``start_once`` fences both create's initial dispatch and retries of an
    unconfirmed start: coalesce with a live execution, but reject a closed one
    rather than run the task again. The server enforces this even if the
    execution closes after our describe.
    """
    if start_once and replace_existing:
        raise ValueError("an initial start retry cannot replace an execution")
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
        gated=start_once or resume_token is not None,
        resume_token=resume_token,
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
        # Initial starts and their retries must not re-run a closed execution.
        id_reuse_policy=(
            WorkflowIDReusePolicy.REJECT_DUPLICATE
            if start_once
            else WorkflowIDReusePolicy.ALLOW_DUPLICATE
        ),
        id_conflict_policy=conflict_policy,
    )
    return handle.id


# What a describe of the per-task workflow id told us about the task's drain.
WF_PROGRESSING = "progressing"
WF_ABSENT = "absent"
WF_CLOSED = "closed"
WF_COMPLETED = "completed"
# Positive type evidence for a later, explicit legacy Re-run on a task whose
# fenced create was voided. A generic CLOSED might just mean start_fenced.
WF_LEGACY_CLOSED = "legacy_closed"
WF_LEGACY_PROGRESSING = "legacy_progressing"


async def describe_eval_task_workflow_async(
    task_id, *, distinguish_completion: bool = False, distinguish_legacy: bool = False
) -> str:
    """Classify the task's workflow: progressing, closed, or never/no longer there.

    ``WF_PROGRESSING`` means a RUNNING execution owns the task and must be left
    alone. ``WF_ABSENT`` (NOT_FOUND — retention expired, or one was never
    started) and ``WF_CLOSED`` (completed / failed / terminated / timed out)
    both mean nothing is draining the task. Any other RPC failure is the
    Temporal service being unreachable, not an answer, so it propagates: the
    caller must skip that task rather than restart it blind. The create
    reconciler distinguishes successful completion from other closed histories
    so it never labels a successful empty execution as a failed start.
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
    legacy = distinguish_legacy and description.workflow_type in (
        "HistoricalEvalTaskWorkflow",
        "ContinuousEvalTaskWorkflow",
    )
    if description.status in (
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.CONTINUED_AS_NEW,
    ):
        return WF_LEGACY_PROGRESSING if legacy else WF_PROGRESSING
    if (
        distinguish_completion
        and description.status == WorkflowExecutionStatus.COMPLETED
    ):
        return WF_COMPLETED
    if description.status in (
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.TIMED_OUT,
    ):
        return WF_LEGACY_CLOSED if legacy else WF_CLOSED
    # An absent/unknown status in a successful reply isn't a closed execution.
    raise RuntimeError("unconfirmed eval task workflow status")


def describe_eval_task_workflow_sync(
    task_id,
    *,
    timeout: float | None = None,
    distinguish_completion: bool = False,
    distinguish_legacy: bool = False,
) -> str:
    from tfc.temporal.common.client import _run_async_in_sync_context

    return _run_async_in_sync_context(
        lambda: asyncio.wait_for(
            describe_eval_task_workflow_async(
                task_id,
                distinguish_completion=distinguish_completion,
                distinguish_legacy=distinguish_legacy,
            ),
            timeout,
        )
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
    "WF_COMPLETED",
    "WF_PROGRESSING",
    "describe_eval_task_workflow_async",
    "describe_eval_task_workflow_sync",
    "start_eval_task_workflow_sync",
    "start_eval_task_workflow_async",
    "signal_pause_eval_task_workflow",
]
