"""Reaper — resets stale ``running`` entries back to ``pending`` so a worker
that died mid-run can't strand them. A poison cap fails an item that
keeps dying after ``max_attempts`` reclaims, so it can't block task completion.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import F
from django.utils import timezone

from tfc.settings.runtime_setting_specs import LONGEST_RUNNING_ENTRY_SECONDS
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask

# The shortest staleness a reap may act on when it does not know whether a
# workflow is draining the task. A reap taken blind races a run it cannot see:
# an entry claimed by an execution that is still progressing, or an activity a
# since-closed execution left in flight on a worker. Retiring such a claim
# requeues an entry whose run is still executing — it is re-claimed and
# evaluated a second time, one of its three reclaims is spent, and the first
# run's paid result is then refused by the write fence.
#
# ``LONGEST_RUNNING_ENTRY_SECONDS`` is the same bound
# ``validate_eval_execution_settings`` holds the sweep's own threshold above:
# the run-entry start-to-close ceiling times its retry attempts.
MIN_STALE_RUNNING_SECONDS = LONGEST_RUNNING_ENTRY_SECONDS + 1


def effective_stale_seconds(
    requested: int, *, workflow_confirmed_stopped: bool = False
) -> int:
    """The staleness a reap really applies — the one place the floor is decided.

    ``workflow_confirmed_stopped`` is evidence, not a preference: the caller
    describes the task's workflow immediately before it starts one and passes
    True only when the server answered that no execution owns the task id
    (absent, or closed). Then the requested threshold is used exactly, so
    Resume, Edit → Save and the sweep's restart reclaim with
    ``ReapInput``'s own 600 s instead of the ninety-minute floor — which is
    what makes those levers do anything at all within an hour and a half of a
    crash.

    What a dead workflow rules out is a *dispatcher*: no execution is claiming
    batches or retrying activities, so nothing new can take the entries this
    reap requeues. It does not rule out a thread. Terminating an execution does
    not kill an activity's Python thread, so an abandoned evaluation may still
    be running; its write is refused by the claim-epoch fence
    (``entries.running_entry_epoch``), so the row stays correct, and the
    residue is a duplicated evaluation *spend* on a task whose worker died
    mid-run. That residue closes with the run-entry thread-leak follow-up, not
    here.

    Without that evidence — a progressing workflow, or a describe that could
    not answer — the floor applies. ``reap_stale_running`` itself stays exact:
    it is the primitive, and this is the policy.
    """
    if workflow_confirmed_stopped:
        return int(requested)
    return max(int(requested), MIN_STALE_RUNNING_SECONDS)


def reap_stale_running(
    task: EvalTask, *, older_than_seconds: int, max_attempts: int
) -> tuple[int, int]:
    """Reclaim entries stuck in ``running`` longer than ``older_than_seconds``.

    Returns ``(requeued, failed)``: under the cap → back to ``pending``
    (attempts incremented); at/over the cap → ``errored`` permanently.
    """
    now = timezone.now()
    cutoff = now - timedelta(seconds=older_than_seconds)
    stale = EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        status=EvalEntryStatus.RUNNING,
        updated_at__lt=cutoff,
    )
    failed = stale.filter(attempts__gte=max_attempts).update(
        status=EvalEntryStatus.ERRORED,
        error=True,
        error_message="reaper: max attempts exceeded",
        updated_at=now,
    )
    requeued = stale.filter(attempts__lt=max_attempts).update(
        status=EvalEntryStatus.PENDING,
        attempts=F("attempts") + 1,
        updated_at=now,
    )
    return requeued, failed
