"""Read-only progress over a task's entries, computed on the fly from the
``(eval_task_id, status)`` index — no denormalized counters to drift."""

from __future__ import annotations

from collections.abc import Mapping
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


def progress_block(counts: Mapping[str, int]) -> dict:
    """The ``progress`` payload, from one status histogram.

    Shared so the two routes that answer about the same task cannot drift: the
    detail serializer computes ``counts`` per task, the root list route reads
    them for a whole page in one grouped query, and both hand the counts here.

    Skipped is neither done nor outstanding. The eval never ran — a mapped span
    attribute was absent, so the entry terminalized before any model call.
    Counting it as done made a task that skipped every row read "100 %
    complete" with zero results; counting it as outstanding would make a
    finished task look stuck. It gets its own tally and stays in the total.
    """
    done = counts.get(EvalEntryStatus.COMPLETED, 0) + counts.get(
        EvalEntryStatus.ERRORED, 0
    )
    skipped = counts.get(EvalEntryStatus.SKIPPED, 0)
    remaining = counts.get(EvalEntryStatus.PENDING, 0) + counts.get(
        EvalEntryStatus.RUNNING, 0
    )
    total = done + skipped + remaining
    return {
        "dispatched": total,
        "completed": done,
        "skipped": skipped,
        "missing": remaining,
        "percent": round(100.0 * done / total, 2) if total else None,
    }


def has_undrained_work(task: EvalTask) -> bool:
    """True while any entry is still pending or running (task not yet drained)."""
    return EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        status__in=[EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING],
    ).exists()
