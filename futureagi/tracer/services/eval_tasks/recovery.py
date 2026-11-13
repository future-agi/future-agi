"""Recovery of eval tasks whose drain stopped: reap, then restart or defer.

The scheduled sweep (``tracer.tasks.eval_task_sweeper``) is the adapter; this
is the policy. Candidates come from ``tracer.selectors.eval_tasks.stranded``.
"""

from __future__ import annotations

from datetime import datetime

import structlog
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from tfc.temporal.eval_tasks.types import ReapInput
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.selectors.eval_tasks.stranded import (
    find_stranded_tasks,
    only_unreclaimable_claims_remain,
    sweepable_statuses,
)
from tracer.services.eval_tasks.reaper import (
    effective_stale_seconds,
    reap_stale_running,
)

logger = structlog.get_logger(__name__)

# A scheduling hint only; losing it restarts the idle ring, never changes safety.
_SWEEP_POSITION_KEY = "eval-task-sweep:position:v1"

# Same poison cap the workflow's own reap uses (``ReapInput.max_attempts``), so
# a row that keeps dying is failed after the same number of reclaims however it
# was reaped.
_MAX_ENTRY_ATTEMPTS = ReapInput.max_attempts

# What the first reap of a workflow this sweep starts applies. The sweep has
# just been told nothing owns the task and hands that answer to the starter,
# which carries it into the run rather than describing again, so the run reaps
# with ``ReapInput``'s own threshold rather than the blind floor.
RESTART_REAP_SECONDS = effective_stale_seconds(
    ReapInput.older_than_seconds, workflow_confirmed_stopped=True
)


class RecoveryInterrupted(Exception):
    """A recovery that had already written rows when it failed.

    The describe is a gate and its failure propagates bare: nothing is written
    before it answers, so a tick that loses it loses nothing to report. Every
    later step can have reaped rows already, and those requeues and the
    attempts they spent are real whether or not the restart that follows them
    succeeds — so the failure has to carry them out to the tick rather than
    discard them. ``entries_requeued`` is the line the runbook tells an
    operator to watch; it must not read 0 for a reap that happened.
    """

    def __init__(self, outcome: dict, cause: Exception):
        super().__init__(str(cause))
        self.outcome = outcome
        self.cause = cause


def continuous_activation_floor(task: EvalTask, *, now: datetime) -> datetime | None:
    """The ``start_time`` a continuous task must be restarted with.

    Coverage begins when the task's current coverage was accepted, never at
    recovery: arrivals between acceptance and the restart belong to the task,
    so the floor is not ``now``. ``start_time`` is that acceptance once
    anything has stamped it (the workflow's first RUNNING transition, a
    conversion in ``update_task_configuration``, or a cursor-resetting rerun in
    ``update_eval_task``); a task that never started falls back to
    ``created_at``.

    With a cursor, the reconciler floors at the cursor and ``start_time`` only
    bounds it from below, so it is kept as it is. Without one, rows written
    before those edits stamped ``start_time`` can still carry an older value: a
    historical run's start, or the previous activation's before a Delete &
    rerun or selection edit whose dispatch failed. The NULL-cursor reconcile
    would then evaluate (and bill) every arrival since that older time, which
    no ordinary dispatch would. ``last_run`` tells the two apart without a
    migration: creation and ``update_eval_task`` stamp it in the transaction
    that accepts the coverage, and the reconciler stamps it only in the pass
    that also sets the cursor, so with no cursor it is the latest acceptance.
    It is a writable API field, hence the cap at ``now``.
    """
    floor = task.start_time or task.created_at
    if task.continuous_cursor is not None or task.last_run is None:
        return floor
    accepted = min(task.last_run, now)
    if floor is None or accepted > floor:
        return accepted
    return floor


def recover_task(task: EvalTask, *, stale_running_seconds: int) -> dict:
    """Ask whether anything is draining the task; if nothing is, reap its stale
    ``running`` entries and restart it — or defer it to a later tick.

    The describe comes first and is a gate, not a hint. An entry is ``RUNNING``
    from the moment its batch is claimed, so a task that is draining normally
    holds a tail of claimed-but-unstarted entries whose stamp is as old as the
    claim — reaping those requeues work a queued activity is about to run, and
    that activity then takes the re-claim and pays for the evaluation twice. By
    asking first the sweep only ever retires a claim no execution owns any
    more, and a healthy task really does cost one describe and nothing else.

    A describe that cannot answer (Temporal unreachable) propagates before
    anything is written. A failure after the reap — the deferral check, the
    status flip, or the start — raises ``RecoveryInterrupted`` carrying the
    reap already performed, so either way the tick's counts describe what it
    actually did.

    The reap still reaches what the workflow-start reaper cannot: a workflow
    that stopped mid-drain leaves entries abandoned in ``running``, and nothing
    else looks at them until something starts a workflow. The one run that can
    still be in flight here belongs to an execution that has since closed —
    bounded by a single activity attempt, because a closed execution dispatches
    no retries — and ``EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS`` is floored to
    exceed it, so *this* reap never requeues that run's entry. The floor is not
    the guarantee, though: the workflow restarted below reaps first at
    ``RESTART_REAP_SECONDS`` (600 s) on the evidence of the same describe —
    handed to the starter, which does not describe again — so a claim older
    than that is reclaimed as soon as it starts, whatever the setting says.
    What makes that safe is the fence — the in-flight run's writes are fenced
    on the claim stamp it took, not merely on ``RUNNING``, so a requeue and a
    re-claim refuse them — and what it costs is one evaluation paid for twice
    (``effective_stale_seconds``). Raising the setting does not remove that
    cost.

    **Deferral.** A workflow that stopped a few minutes ago leaves claims
    younger than both this reap and the ``RESTART_REAP_SECONDS`` the restarted
    run's own reap applies. If those are all the task holds, a restart claims
    nothing and cannot finalize. So the task is left exactly as it is — still
    sweepable, nothing written — and a later tick restarts it once a claim is
    reclaimable. ``deferred`` reports it.

    The restart coalesces (``replace_existing=False``) rather than terminating:
    the workflow id is per task, so the Temporal server decides atomically
    whether a live execution already owns it. A describe is a read taken a
    moment earlier, so terminating on it would kill a workflow a user started
    in between — the exact failure this whole change exists to prevent. For the
    same reason ``restarted`` counts starts *issued*: one that coalesced onto an
    execution started in the gap is counted too, because the server resolves
    that and does not report which way it went. Coalescing is also what lets
    the describe's answer travel with the start: a start absorbed by an
    execution begun in the gap is discarded with its input, so the answer only
    reaches a run that started with nothing draining.
    """
    from tfc.temporal.eval_tasks.client import (
        WF_PROGRESSING,
        describe_eval_task_workflow_sync,
        start_eval_task_workflow_sync,
    )

    outcome = {
        "requeued": 0,
        "failed": 0,
        "restarted": False,
        "progressing": False,
        "deferred": False,
        "skipped": False,
    }
    if describe_eval_task_workflow_sync(task.id) == WF_PROGRESSING:
        # Reported, not merely skipped: a healthy draining task is a candidate
        # that costs one describe and nothing else, so without this the tick
        # has no field that separates the fleet's ordinary working set from
        # the tasks it acted on.
        outcome["progressing"] = True
        return outcome

    # Pause/delete updates lock this same task row. Read desired state under
    # that lock before touching entries, not from the pre-describe candidate.
    with transaction.atomic():
        current = (
            EvalTask.no_workspace_objects.select_for_update()
            .filter(id=task.id, status__in=sweepable_statuses())
            .first()
        )
        if current is None:
            outcome["skipped"] = True
            return outcome
        task = current
        outcome["requeued"], outcome["failed"] = reap_stale_running(
            task,
            older_than_seconds=effective_stale_seconds(
                stale_running_seconds, workflow_confirmed_stopped=True
            ),
            max_attempts=_MAX_ENTRY_ATTEMPTS,
        )

    try:
        if only_unreclaimable_claims_remain(
            task, reclaimable_after_seconds=RESTART_REAP_SECONDS
        ):
            outcome["deferred"] = True
            return outcome

        with transaction.atomic():
            current = (
                EvalTask.no_workspace_objects.select_for_update()
                .filter(id=task.id, status__in=sweepable_statuses())
                .first()
            )
            if current is None:
                outcome["skipped"] = True
                return outcome
            task = current
            if task.run_type == RunType.CONTINUOUS:
                # Continuous coverage starts at acceptance/activation, never
                # at recovery (see ``continuous_activation_floor``). Match the
                # workflow's guarded startup transition, retaining partial (or
                # NULL) cursors and existing results through catch-up.
                floor = continuous_activation_floor(task, now=timezone.now())
                if floor != task.start_time and task.start_time is not None:
                    logger.info(
                        "eval_task_sweep_activation_floor_raised",
                        task_id=str(task.id),
                        stored_start_time=task.start_time.isoformat(),
                        activation_floor=floor.isoformat(),
                    )
                task.start_time = floor
                task.status = EvalTaskStatus.RUNNING
                EvalTask.no_workspace_objects.filter(id=task.id).update(
                    status=task.status, start_time=task.start_time
                )
            elif task.status == EvalTaskStatus.FAILED:
                task.status = EvalTaskStatus.PENDING
                EvalTask.no_workspace_objects.filter(id=task.id).update(
                    status=task.status
                )

        # Commit state before dispatch (the worker must see it), then recheck
        # on the primary: replica lag must neither suppress a valid restart nor
        # hide a committed pause/delete. A later pause wins the guarded status
        # transition and active-state checks; never replace an execution.
        if not (
            EvalTask.no_workspace_objects.filter(id=task.id, status=task.status)
            .using("default")
            .exists()
        ):
            outcome["skipped"] = True
            return outcome

        # The describe above is the evidence the restarted run's first reap
        # needs, and it has answered: nothing owns the task. Handed over
        # rather than asked again, so the deferral check's 600 s is the
        # threshold the run really applies — a second describe that failed
        # would drop it to the blind floor after this tick had admitted the
        # restart on 600 s.
        start_eval_task_workflow_sync(
            task, replace_existing=False, workflow_confirmed_stopped=True
        )
    except Exception as exc:
        raise RecoveryInterrupted(outcome, exc) from exc
    outcome["restarted"] = True
    return outcome


def recover_stranded_tasks(*, limit: int, stale_running_seconds: int) -> dict:
    """One sweep tick: recover every candidate, isolate each one's failure.

    One task's failure (a Temporal describe against an unreachable service,
    say) must not cost the others their recovery; the next tick retries it.
    Each absorbed failure is logged with its traceback, because the counts
    alone say that something failed and never why.
    """
    try:
        position = cache.get(_SWEEP_POSITION_KEY) or {}
    except Exception:
        logger.warning("eval_task_sweep_position_read_failed", exc_info=True)
        position = {}
    tasks = find_stranded_tasks(
        limit=limit,
        idle_after=position.get("idle_after"),
        idle_first=position.get("idle_first", False),
    )
    counts = {
        "backlog_candidates": 0,
        "idle_continuous_candidates": 0,
        "skipped": 0,
        "progressing": 0,
        "restarted": 0,
        "deferred": 0,
        "entries_requeued": 0,
        "entries_poisoned": 0,
        "errors": 0,
    }
    for task in tasks:
        kind = task.sweep_candidate_kind
        counts[f"{kind}_candidates"] += 1
        if kind == "idle_continuous":
            position["idle_after"] = str(task.id)
        try:
            outcome = recover_task(task, stale_running_seconds=stale_running_seconds)
        except RecoveryInterrupted as exc:
            # The restart is gone, but the reap before it is not: those rows
            # are pending again and have spent an attempt, and the next tick
            # recovers them as ordinary stranded work.
            counts["errors"] += 1
            logger.warning(
                "eval_task_sweep_task_failed",
                task_id=str(task.id),
                error_type=type(exc.cause).__name__,
                exc_info=exc.cause,
            )
            outcome = exc.outcome
        except Exception as exc:
            counts["errors"] += 1
            logger.warning(
                "eval_task_sweep_task_failed",
                task_id=str(task.id),
                error_type=type(exc).__name__,
                exc_info=exc,
            )
            continue
        counts["skipped"] += int(outcome["skipped"])
        counts["progressing"] += int(outcome["progressing"])
        counts["restarted"] += int(outcome["restarted"])
        counts["deferred"] += int(outcome["deferred"])
        counts["entries_requeued"] += outcome["requeued"]
        counts["entries_poisoned"] += outcome["failed"]
    position["idle_first"] = not position.get("idle_first", False)
    try:
        cache.set(_SWEEP_POSITION_KEY, position, timeout=None)
    except Exception:
        logger.warning("eval_task_sweep_position_write_failed", exc_info=True)
    return {"candidates": len(tasks), **counts}


__all__ = [
    "RESTART_REAP_SECONDS",
    "RecoveryInterrupted",
    "continuous_activation_floor",
    "recover_stranded_tasks",
    "recover_task",
]
