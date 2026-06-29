"""Read-only progress over a task's entries, computed on the fly from the
``(eval_task_id, status)`` index — no denormalized counters to drift."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count

from tracer.models.observation_span import EvalEntryStatus, EvalLogger

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask


def count_by_status(task: EvalTask) -> dict[str, int]:
    rows = (
        EvalLogger.objects.filter(eval_task_id=str(task.id))
        .values("status")
        .annotate(n=Count("id"))
    )
    return {row["status"]: row["n"] for row in rows}


def has_undrained_work(task: EvalTask) -> bool:
    """True while any entry is still pending or running (task not yet drained)."""
    return EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        status__in=[EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING],
    ).exists()
