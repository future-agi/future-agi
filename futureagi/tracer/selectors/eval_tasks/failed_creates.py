"""Read side of withdrawing the rows that failed creates left behind.

Until create withdrew a task whose workflow start raised, that row stayed live:
a continuous task its owner was told "could not be created", still PENDING and
never started. The stranded sweep activates idle continuous tasks from their
creation time, so such a row would evaluate, and bill, every in-scope span
since then, beside the task a retry created. ``withdraw_failed_create_eval_tasks``
withdraws them before the sweep is enabled; the writes live in
``tracer.services.eval_tasks.withdrawal``.

The predicate is exactly "never started": no start or end stamp, no cursor, no
result or task-logger row in any state (soft-deleted ones included). A task
that ever ran carries at least one of those, so nothing here can match it. Two
things the database cannot say are left to the caller: whether Temporal holds a
workflow for the task, and whether a create is still between saving the row
and starting its workflow (hence ``created_before``).
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Exists, OuterRef, QuerySet, TextField
from django.db.models.functions import Cast

from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalLogger

FAILED_CREATE_PREDICATE = (
    "run_type='continuous' AND status='pending' AND deleted=false"
    " AND start_time IS NULL AND end_time IS NULL AND continuous_cursor IS NULL"
    " AND no eval result row for the task (any status, soft-deleted included)"
    " AND no task logger row for the task (soft-deleted included)"
    " AND created_at < created_before"
    " AND Temporal has no workflow for eval-task-<id> (describe: NOT_FOUND)"
)


def failed_create_candidates(*, created_before: datetime) -> QuerySet[EvalTask]:
    """Live tasks that never started, created before ``created_before``.

    Every clause of ``FAILED_CREATE_PREDICATE`` except the Temporal one. Both
    logger tables have no workspace column, so ``all_objects`` is every row,
    soft-deleted included.
    """
    results = EvalLogger.all_objects.filter(
        eval_task_id=Cast(OuterRef("id"), output_field=TextField())
    )
    loggers = EvalTaskLogger.all_objects.filter(eval_task_id=OuterRef("id"))
    return EvalTask.no_workspace_objects.filter(
        ~Exists(results),
        ~Exists(loggers),
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        start_time__isnull=True,
        end_time__isnull=True,
        continuous_cursor__isnull=True,
        created_at__lt=created_before,
    )


__all__ = ["FAILED_CREATE_PREDICATE", "failed_create_candidates"]
