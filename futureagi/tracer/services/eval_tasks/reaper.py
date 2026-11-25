"""Reaper — resets stale ``running`` entries back to ``pending`` so a worker
that died mid-run can't strand them. A poison cap fails an item that
keeps dying after ``max_attempts`` reclaims, so it can't block task completion.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import structlog
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from tfc.settings.runtime_setting_specs import LONGEST_RUNNING_ENTRY_SECONDS
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask

logger = structlog.get_logger(__name__)

_GAVE_UP = "reaper: max attempts exceeded"

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


class StaleRunningClaims:
    """One cutoff for a reap; retryable and terminal writes can commit separately."""

    def __init__(self, task: EvalTask, *, older_than_seconds: int, max_attempts: int):
        self.task = task
        self.max_attempts = max_attempts
        self.now = timezone.now()
        self.failed = 0
        self.stale = EvalLogger.objects.filter(
            eval_task_id=str(task.id),
            status=EvalEntryStatus.RUNNING,
            updated_at__lt=self.now - timedelta(seconds=older_than_seconds),
        )

    def fail_exhausted(self, *, record_answers: bool = True) -> int:
        """Fail the claims that used up their attempts.

        A caller holding the task row lock passes ``record_answers=False`` and
        calls ``record_failed_answers`` once that lock is released: recording
        locks the Instant eval run row, and Instant eval cancel, Resume and
        retry lock that run row before the task row.
        """
        self.failed = self.stale.filter(attempts__gte=self.max_attempts).update(
            status=EvalEntryStatus.ERRORED,
            error=True,
            error_message=_GAVE_UP,
            updated_at=self.now,
        )
        if record_answers:
            self.record_failed_answers()
        return self.failed

    def record_failed_answers(self) -> None:
        """Copy the Instant answers of every given-up entry still missing one.

        Chosen from the entries themselves, not from this reap's count: when a
        worker exits after the reap commits and before this runs, the retried
        activity (or the next reap, or the sweep) finds them and copies them.
        """
        _record_instant_answers(self.task)

    def requeue_retryable(self) -> int:
        return self.stale.filter(attempts__lt=self.max_attempts).update(
            status=EvalEntryStatus.PENDING,
            attempts=F("attempts") + 1,
            updated_at=self.now,
        )


def reap_stale_running(
    task: EvalTask,
    *,
    older_than_seconds: int,
    max_attempts: int,
    answers_after_lock: list[StaleRunningClaims] | None = None,
) -> tuple[int, int]:
    """Return (requeued, failed) for a workflow's startup or periodic reap.

    A caller that holds the task row lock passes ``answers_after_lock`` and
    calls ``record_failed_answers`` on what it collects after releasing the
    lock (see ``StaleRunningClaims.fail_exhausted``).
    """
    claims = StaleRunningClaims(
        task, older_than_seconds=older_than_seconds, max_attempts=max_attempts
    )
    failed = claims.fail_exhausted(record_answers=answers_after_lock is None)
    if answers_after_lock is not None:
        answers_after_lock.append(claims)
    return claims.requeue_retryable(), failed


def _record_instant_answers(task: EvalTask) -> None:
    """An Instant eval run shows the entries given up on as failed (not still
    pending) while it runs. Never fails the reap.

    The entries are locked and rechecked in the transaction that copies their
    answers, and one a Retry is requeueing is skipped (``lock_unanswered``):
    reading them first and copying later could bring back an answer the
    Retry just dropped."""
    from tracer.services.instant_eval_answers import (
        is_instant_task,
        lock_unanswered,
        record_answers,
    )

    try:
        if not is_instant_task(task.id):
            return
        # On the primary (a raw cursor of the default connection): these
        # rows were failed a moment ago, and a replica may not have them yet.
        with transaction.atomic(using="default"):
            ids = lock_unanswered(
                task.id, status=EvalEntryStatus.ERRORED, error_message=_GAVE_UP
            )
            if ids:
                record_answers(task.id, ids)
    except Exception:
        logger.warning(
            "instant_eval_reaped_answers_failed", task_id=str(task.id), exc_info=True
        )
