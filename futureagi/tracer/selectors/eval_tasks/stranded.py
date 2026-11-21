"""Read side of the stranded-work recovery sweep.

Which tasks the sweep looks at, and whether a task it found holds anything a
restarted workflow could act on. Writes — the reap, the status flip, the
restart — live in ``tracer.services.eval_tasks.recovery``.
"""

from __future__ import annotations

from collections.abc import Iterable
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

# Every reconcile pass of a live continuous workflow parks its cursor
# ``CONTINUOUS_CURSOR_OVERLAP`` (5 min) behind now. For a RUNNING task, a cursor
# newer than six times that proves a workflow polled recently, so the idle
# check skips it. A PENDING task's cursor may belong to an execution that
# ended before a Resume or rerun asked for a new one, so it proves nothing. An
# older or missing cursor proves nothing either way (a live workflow can be
# parked on read-budget or database deferrals, or draining a long backlog), so
# Temporal's describe still decides.
STALE_CONTINUOUS_CURSOR = timedelta(minutes=30)


def sweepable_statuses() -> list[str]:
    statuses = list(_SWEEPABLE)
    if getattr(settings, "EVAL_TASK_SWEEP_RECOVER_FAILED", False):
        statuses.append(EvalTaskStatus.FAILED)
    return statuses


def automatic_recovery_eligibility() -> Q:
    """Require an existing activation/coverage marker for every recovery path.

    This is not Temporal acknowledgement: conversions and explicit reruns can
    stamp coverage before dispatch. Conversely, last_run, age, entries, cursor
    presence and Temporal NOT_FOUND cannot make an unstamped row eligible.
    Legacy and accepted-but-never-activated rows deliberately need an explicit
    owner-directed activation; recovery must not invent one for a failed create.
    Managers still enforce soft-delete exclusion.
    """
    return Q(status__in=sweepable_statuses(), start_time__isnull=False)


def find_stranded_tasks(
    *,
    limit: int | None = None,
    idle_after: str | None = None,
    idle_first: bool = False,
    held_ids: Iterable[str] = (),
    held_failed_ids: Iterable[str] = (),
) -> list[EvalTask]:
    """Bounded liveness checks for backlog and idle continuous tasks.

    Each group gets half the budget, borrowing unused slots from the other.
    Backlog keeps oldest-entry priority; idle tasks walk a UUID ring, resuming
    after the previous tick's last check. Old live idle tasks must not occupy
    every tick forever. With a one-slot cap, the caller alternates groups.

    Cursor age only rules a task *out*: an idle continuous task is checked
    when it is not RUNNING (PENDING after a Resume or rerun, or FAILED
    when opted in), when its cursor is NULL (no reconcile has parked one yet),
    or when the cursor is older than ``STALE_CONTINUOUS_CURSOR``. A fresher
    cursor on a RUNNING task shows that its workflow polled within that window,
    so describing it would only spend the budget; a workflow that died is
    checked at most that long after its last poll. A PENDING task is always
    checked, because its cursor may predate the start it is waiting for. A stale cursor is never taken as proof of death: a healthy idle
    workflow can legitimately have one, and only Temporal's describe
    establishes liveness. The idle ring is ordered by id, not by cursor age,
    so live tasks with old cursors cannot hold the head slots on every tick.
    Drained historical tasks stay excluded. Both managers exclude soft-deleted
    rows and ignore ambient workspace scope.

    One rule set for every candidate, whichever group found it: an existing
    start_time marker is mandatory (including for backlog and opted-in FAILED
    rows), and each task appears at most once per tick (a task with undrained
    entries is backlog, never also idle). Its lifecycle is rechecked here and
    again under a row lock in ``recover_task``. A restart coalesces onto any
    live execution rather than replacing it.

    ``held_ids`` are tasks Temporal reported live within the hold window, and
    ``held_failed_ids`` failed tasks the sweep already restarted as often as it
    may; both are left out before the cap is applied, so a task the describe
    would skip, or a failure that repeats deterministically, never takes a
    slot from one that needs recovery. ``held_failed_ids`` applies only while
    the task is still FAILED.
    """
    limit = limit if limit is not None else int(settings.EVAL_TASK_SWEEP_MAX_TASKS)
    if limit <= 0:
        return []
    tasks = EvalTask.no_workspace_objects.filter(
        automatic_recovery_eligibility()
    ).exclude(id__in=list(held_ids))
    held_failed_ids = list(held_failed_ids)
    if held_failed_ids:
        tasks = tasks.exclude(status=EvalTaskStatus.FAILED, id__in=held_failed_ids)
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
        .filter(
            ~Q(status=EvalTaskStatus.RUNNING)
            | Q(continuous_cursor__isnull=True)
            | Q(continuous_cursor__lt=timezone.now() - STALE_CONTINUOUS_CURSOR)
        )
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
    # The two groups are disjoint by construction (idle excludes any task with
    # undrained entries); popping keeps a task from being swept twice anyway.
    for task_id, kind in chosen:
        task = by_id.pop(task_id, None)
        if task is not None:
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
    "STALE_CONTINUOUS_CURSOR",
    "automatic_recovery_eligibility",
    "find_stranded_tasks",
    "only_unreclaimable_claims_remain",
    "sweepable_statuses",
]
