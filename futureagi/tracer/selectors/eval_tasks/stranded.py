"""Read side of the stranded-work recovery sweep.

Which tasks the sweep looks at, and whether a task it found holds anything a
restarted workflow could act on. Writes — the reap, the status flip, the
restart — live in ``tracer.services.eval_tasks.recovery``.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import Max, Q, Subquery, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

# Statuses whose undrained work the sweep may re-enter without asking anyone.
_SWEEPABLE = (EvalTaskStatus.RUNNING, EvalTaskStatus.PENDING)

_UNDRAINED = (EvalEntryStatus.PENDING, EvalEntryStatus.RUNNING)


def sweepable_statuses() -> list[str]:
    statuses = list(_SWEEPABLE)
    if getattr(settings, "EVAL_TASK_SWEEP_RECOVER_FAILED", False):
        statuses.append(EvalTaskStatus.FAILED)
    return statuses


def find_stranded_tasks(*, limit: int | None = None) -> list[EvalTask]:
    """Tasks in a sweepable status that still hold undrained entries.

    Ordered by the oldest last-touched entry first, so a task frozen for days
    is always served before tasks that are draining normally — otherwise a busy
    fleet would spend every tick's budget on healthy tasks and never reach the
    stranded one. A healthy task selected anyway costs one describe and nothing
    else: ``recover_task`` asks before it writes, so a progressing workflow's
    entries are neither reaped nor restarted.

    ``candidates`` therefore counts every task in a sweepable status holding
    undrained entries — which, on a working fleet, is mostly tasks that are
    draining normally. It is the sweep's *working set*, not a count of stranded
    tasks: the describe is what tells the two apart, and it happens per
    candidate in ``recover_task``. ``candidates - progressing`` is what the
    tick acted on.

    It also never counts the rest: a paused, delete-status or failed task
    holding undrained entries, or an entry whose task row is gone, contributes
    nothing and produces no event. ``candidates: 0`` means nothing sweepable
    holds undrained work at all, not that nothing is stranded.

    The per-tick cap is applied **after** the sweepable-status filter, not
    before it. A task the sweep refuses to act on — paused, delete-status,
    finished with leftovers, or an entry pointing at a task row that no longer
    exists — never drains, so its entries' ``updated_at`` never advances and it
    sorts oldest on every tick for ever. Costing such a task against the cap
    would hand it a head slot permanently: fill the cap with them and every
    tick returns nothing, silently, for as long as they exist. The runbook
    tells operators not to sweep paused tasks, so paused tasks holding
    undrained entries are the expected steady state, not an edge case.

    ``no_workspace_objects``: this is a system-wide job and must not inherit a
    leaked workspace scope. Both managers exclude soft-deleted rows, so the
    entries a Delete & rerun wiped cannot read as undrained work, and a
    soft-deleted task cannot be swept.
    """
    limit = limit if limit is not None else int(settings.EVAL_TASK_SWEEP_MAX_TASKS)
    # ``eval_task_id`` is a CharField holding the task uuid's text form (every
    # writer stamps ``str(task.id)``), so the sweepable set is cast to text to
    # join against it. As a subquery rather than a materialized id list: the
    # candidate read has to be narrowed to sweepable tasks *inside* the query
    # the cap slices, which is the whole point of the ordering above.
    sweepable_task_ids = (
        EvalTask.no_workspace_objects.filter(status__in=sweepable_statuses())
        .annotate(id_text=Cast("id", output_field=TextField()))
        .values("id_text")
        # The model's Meta ordering would otherwise ride along inside the
        # semi-join, sorting a set nothing reads in order.
        .order_by()
    )
    # ``isnull`` / ``exclude("")`` are redundant beside the semi-join — neither
    # value can be in a set of rendered uuids — and are kept only to drop those
    # rows before the join. They are no longer what stops an empty string
    # reaching a UUID column: nothing compares ``eval_task_id`` to a uuid any
    # more.
    stranded_ids = [
        row["eval_task_id"]
        for row in EvalLogger.no_workspace_objects.filter(
            status__in=_UNDRAINED,
            eval_task_id__isnull=False,
            eval_task_id__in=Subquery(sweepable_task_ids),
        )
        .exclude(eval_task_id="")
        .values("eval_task_id")
        .annotate(last_touched=Max("updated_at"))
        .order_by("last_touched")[:limit]
    ]
    if not stranded_ids:
        return []
    # Re-read as model instances. The status filter is repeated so a task
    # paused or deleted between the two reads is dropped rather than swept.
    by_id = {
        str(task.id): task
        for task in EvalTask.no_workspace_objects.filter(
            id__in=stranded_ids, status__in=sweepable_statuses()
        )
    }
    return [by_id[task_id] for task_id in stranded_ids if task_id in by_id]


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
