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

``EVAL_TASK_SWEEP_MAX_TASKS=0`` disables the sweep. That is the rollback that
survives a restart: pausing the schedule in Temporal takes effect at once but
is undone by the next backend container start, which re-registers every
schedule with its state rebuilt from config.
"""

from __future__ import annotations

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity
from tracer.selectors.eval_tasks.stranded import (
    find_stranded_tasks,
    sweepable_statuses,
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


__all__ = [
    "RecoveryInterrupted",
    "find_stranded_tasks",
    "recover_task",
    "sweep_stranded_eval_tasks",
    "sweepable_statuses",
]
