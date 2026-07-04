"""Reaper — resets stale ``running`` entries back to ``pending`` so a worker
that died mid-run can't strand them. A poison cap fails an item that
keeps dying after ``max_attempts`` reclaims, so it can't block task completion.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import F
from django.utils import timezone

from tracer.models.observation_span import EvalEntryStatus, EvalLogger

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask


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
