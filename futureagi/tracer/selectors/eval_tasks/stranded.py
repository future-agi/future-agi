"""Read side of the stranded-work recovery sweep.

Which tasks the sweep looks at, and whether a task it found holds anything a
restarted workflow could act on. Writes — the reap, the status flip, the
restart — live in ``tracer.services.eval_tasks.recovery``.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, Max, OuterRef, Q, Subquery, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

# Statuses whose undrained work the sweep may re-enter without asking anyone.
_SWEEPABLE = (EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING)

_UNDRAINED = (EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING)


def sweepable_statuses() -> list[str]:
    statuses = list(_SWEEPABLE)
    if getattr(settings, "EVAL_TASK_SWEEP_RECOVER_FAILED", False):
        statuses.append(EvalTaskStatus.FAILED)
    return statuses


def find_stranded_tasks(
    *, limit: int | None = None, idle_after: str | None = None, idle_first: bool = False
) -> list[EvalTask]:
    """Bounded liveness checks for backlog and idle continuous tasks.

    Each group gets half the budget, borrowing unused slots from the other.
    Backlog keeps oldest-entry priority; idle tasks walk a UUID ring, resuming
    after the previous tick's last check. Old live idle tasks must not occupy
    every tick forever. With a one-slot cap, the caller alternates groups.

    No cursor-age test: a never-dispatched PENDING task has no cursor, and a
    healthy idle workflow can legitimately have an old one. Only Temporal's
    describe can establish liveness. Drained historical tasks stay excluded.
    Both managers exclude soft-deleted rows and ignore ambient workspace scope.
    """
    limit = limit if limit is not None else int(settings.EVAL_TASK_SWEEP_MAX_TASKS)
    if limit <= 0:
        return []
    tasks = EvalTask.no_workspace_objects.filter(status__in=sweepable_statuses())
    # EvalLogger's task id is text, not a FK. Never cast arbitrary logger ids
    # to UUID: legacy rows can contain blank or invalid ids.
    sweepable_task_ids = (
        tasks.annotate(id_text=Cast("id", output_field=TextField()))
        .values("id_text")
        .order_by()
    )
    backlog_ids = list(
        EvalLogger.no_workspace_objects.filter(
            status__in=_UNDRAINED,
            eval_task_id__in=Subquery(sweepable_task_ids),
        )
        .values("eval_task_id")
        .annotate(last_touched=Max("updated_at"))
        .order_by("last_touched", "eval_task_id")
        .values_list("eval_task_id", flat=True)[:limit]
    )
    backlog_budget = limit // 2 if idle_first else (limit + 1) // 2
    idle_budget = limit - min(len(backlog_ids), backlog_budget)
    undrained = EvalLogger.no_workspace_objects.filter(
        eval_task_id=Cast(OuterRef("id"), output_field=TextField()),
        status__in=_UNDRAINED,
    )
    idle = (
        tasks.filter(run_type=RunType.CONTINUOUS)
        .exclude(id__in=backlog_ids)
        .filter(~Exists(undrained))
        .order_by("id")
    )
    idle_ids = list(
        (idle.filter(id__gt=idle_after) if idle_after else idle).values_list(
            "id", flat=True
        )[:idle_budget]
    )
    if idle_after and len(idle_ids) < idle_budget:
        idle_ids += list(
            idle.filter(id__lte=idle_after).values_list("id", flat=True)[
                : idle_budget - len(idle_ids)
            ]
        )
    chosen = [(str(i), "idle_continuous") for i in idle_ids]
    chosen += [(i, "backlog") for i in backlog_ids[: limit - len(chosen)]]
    # Recheck lifecycle after the candidate reads, not just inside the cap.
    by_id = {str(task.id): task for task in tasks.filter(id__in=[i for i, _ in chosen])}
    result = []
    for task_id, kind in chosen:
        if task_id in by_id:
            task = by_id[task_id]
            task.sweep_candidate_kind = kind
            result.append(task)
    return result


def only_unreclaimable_claims_remain(
    task: EvalTask, *, reclaimable_after_seconds: int
) -> bool:
    """Whether every undrained entry is a claim too young to reclaim yet.

    True only when the task holds undrained work and all of it is ``RUNNING``
    with a claim stamp younger than ``reclaimable_after_seconds``: a workflow
    started now would find nothing to claim, reap nothing, and could not
    finalize. False when there is pending work, a claim old enough to reap,
    or nothing undrained at all — the last because only a run can finalize a
    task whose last entries were just poisoned.
    """
    cutoff = timezone.now() - timedelta(seconds=reclaimable_after_seconds)
    undrained = EvalLogger.no_workspace_objects.filter(
        eval_task_id=str(task.id), status__in=_UNDRAINED
    )
    actionable = undrained.filter(
        Q(status=EvalEntryStatus.PENDING) | Q(updated_at__lt=cutoff)
    )
    return undrained.exists() and not actionable.exists()


__all__ = [
    "find_stranded_tasks",
    "only_unreclaimable_claims_remain",
    "sweepable_statuses",
]
