"""Scheduled recovery sweep for eval tasks whose drain stopped.

An eval task drains through exactly one Temporal workflow, and every start of
one is user-initiated (create, resume, edit). The reaper that reclaims entries
abandoned in ``running`` runs once per execution — at the historical
workflow's first start, deliberately skipped across its continue-as-new hops,
and once per hop of the continuous one — so a workflow that stops (failed,
terminated, lost with its worker) leaves every remaining entry frozen with
nothing watching.
``has_undrained_work`` exists but is only ever read from inside a running
workflow. This sweep is the missing out-of-band recovery, and the only thing
that makes a stranded task visible at all.

Per tick, bounded and idempotent:

* take tasks in a sweepable status that still have undrained entries, oldest
  activity first, capped at ``EVAL_TASK_SWEEP_MAX_TASKS``
  (``tracer.selectors.eval_tasks.stranded``);
* ask Temporal whether a workflow is progressing, and leave that task alone if
  one is — a healthy task costs one describe and nothing else;
* for the rest, reclaim entries stuck ``running`` past
  ``EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS`` and restart the workflow — unless
  all that is left are claims too young for the restarted run to reclaim,
  which are deferred to a later tick (``tracer.services.eval_tasks.recovery``).

Asking before reaping is what makes the reap safe to run on a timer. An entry
is ``RUNNING`` from the moment its batch is claimed, not from the moment its
run starts: ``claim_pending_batch`` stamps a whole batch at once and the drain
runs ``max_concurrent`` of them at a time, so a batch tail waits several waves
under a frozen claim stamp. Reaping such a task would requeue an entry whose
own activity is still queued, and that activity would then take the re-claim
and pay for the evaluation a second time. The sweep therefore only ever
retires claims belonging to an execution that is no longer running.

``paused`` and ``deleted`` tasks are never touched: restarting them spends
evaluation calls on work their owner stopped on purpose. ``failed`` is out of
scope for the same reason and is opt-in through
``EVAL_TASK_SWEEP_RECOVER_FAILED``; the Resume button recovers one explicitly.

``EVAL_TASK_SWEEP_MAX_TASKS=0`` disables the sweep. Pausing the schedule in
Temporal also stops it at once, and the pause outlives later schedule
registrations (every deploy's one-shot ``SERVICE_TYPE=temporal-schedules``
job keeps an existing schedule's paused state); only the setting also holds if
the schedule is deleted and re-created.
"""

from __future__ import annotations

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity
from tracer.selectors.eval_tasks.stranded import (
    find_stranded_tasks,
    sweepable_statuses,
)
from tracer.services.eval_tasks.recalculate import (
    recheck_put_backs,
    recover_stale_recalculations,
)
from tracer.services.eval_tasks.recovery import (
    RecoveryInterrupted,
    recover_stranded_tasks,
    recover_task,
)

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=600, queue="tasks_s", max_retries=0)
def sweep_stranded_eval_tasks():
    """Restart the workflow of every task whose drain stopped. Counts only.

    ``max_retries=0``: the next tick recovers a sweep-level failure, and one
    task's failure (a Temporal describe against an unreachable service, say)
    must not cost the others their recovery.

    ``EVAL_TASK_SWEEP_MAX_TASKS=0`` turns the sweep off. It reports that as its
    own event rather than as an empty tick, because "disabled" and "nothing is
    stranded" are the two readings an operator has to tell apart, and this job
    only ever speaks in counts.

    ``candidates`` is the working set the tick read, healthy tasks included;
    ``progressing`` is how many of them had a live workflow and were left
    alone, and ``deferred`` how many held only claims too young to reclaim yet.
    ``restarted`` is what the sweep acted on.
    """
    limit = int(settings.EVAL_TASK_SWEEP_MAX_TASKS)
    if limit <= 0:
        logger.info("eval_task_sweep_disabled")
        return {
            "candidates": 0,
            "progressing": 0,
            "restarted": 0,
            "deferred": 0,
            "entries_requeued": 0,
            "entries_poisoned": 0,
            "errors": 0,
            "disabled": True,
        }
    result = recover_stranded_tasks(
        limit=limit,
        stale_running_seconds=int(settings.EVAL_TASK_SWEEP_STALE_RUNNING_SECONDS),
    )
    result["disabled"] = False
    logger.info("eval_task_sweep_completed", **result)
    return result


@temporal_activity(time_limit=300, queue="tasks_s", max_retries=0)
def sweep_stale_recalculations():
    """Finish Recalculates whose request died mid-eval. Counts only.

    Recalculate runs the eval inside the user's request, after retiring the
    previous result. A request killed meanwhile (a deploy's SIGKILL, an OOM)
    leaves that result retired:
    * on an eval-task result, with its replacement ``RUNNING``; nothing in the
      engine finishes that on a completed or paused task;
    * on a result with no task (an inline Observe eval, an experiment span),
      with no live result for the span and config at all.
    This puts the previous result back, or keeps a new result that completed,
    once the recalculation is older than any run may last; a new result that
    is already there is kept after two minutes
    (``tracer.services.eval_tasks.recalculate``).

    Then it re-reads the parents of results put back in the last hour, and
    retires one whose span, trace, session or eval config a delete removed
    after the put-back's own checks (``recheck_put_backs``). Its counts are
    under ``put_back_recheck``.

    It spends nothing (no eval runs), so it is not gated on
    ``EVAL_TASK_SWEEP_MAX_TASKS``; pausing its schedule stops it.
    ``max_retries=0``: the next tick retries what this one could not finish.
    """
    # One part failing must not cost the other its tick, either way round.
    try:
        result = recover_stale_recalculations()
    except Exception:
        logger.exception("recalculate_recovery_failed")
        result = {"recovery_failed": True}
    try:
        result["put_back_recheck"] = recheck_put_backs()
    except Exception:
        logger.exception("recalculate_put_back_recheck_failed")
        result["put_back_recheck"] = None
    logger.info("recalculate_sweep_completed", **result)
    return result


__all__ = [
    "RecoveryInterrupted",
    "find_stranded_tasks",
    "recover_task",
    "sweep_stale_recalculations",
    "sweep_stranded_eval_tasks",
    "sweepable_statuses",
]
