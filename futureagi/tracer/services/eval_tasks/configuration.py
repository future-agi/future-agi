"""Persist validated evaluation-task edits and their coverage boundary."""

from datetime import datetime

from django.db import router, transaction
from django.utils import timezone

from tracer.models.eval_task import EvalTask, RunType


def continuous_coverage_floor(task: EvalTask, *, now: datetime) -> datetime | None:
    """Where a continuous task's cursor-less reconcile must start.

    ``start_time`` (``created_at`` before the first start) is the floor this
    code persists whenever it accepts coverage: at creation, at a conversion,
    and at a rerun that resets the cursor. Rows saved before that rule can
    carry an older ``start_time`` and no cursor: the historical run's start
    after a conversion, or the previous activation after a Delete & rerun,
    whose dispatch failed. The previous code restamped ``start_time`` when the
    task next started, so it never evaluated anything from before that edit.
    Starting such a row at its stored ``start_time`` would evaluate, and bill,
    every arrival since the older time.

    ``last_run`` identifies those rows without a migration. While there is no
    cursor it is the latest acceptance: the accepting edit stamps it, the
    reconciler stamps it only in the pass that also parks a cursor, and an
    edit that keeps the floor leaves it alone (``update_task_configuration``).
    So the floor is never earlier than ``last_run``, which is capped at ``now``
    because it is a writable API field. With a cursor, the reconciler starts at
    the cursor and the stored floor stands. ``_set_task_status_sync`` applies
    the same rule in SQL at the PENDING -> RUNNING transition.
    """
    floor = task.start_time or task.created_at
    if task.continuous_cursor is not None or task.last_run is None:
        return floor
    accepted = min(task.last_run, now)
    if floor is None or accepted > floor:
        return accepted
    return floor


def update_task_configuration(task: EvalTask, changes: dict) -> EvalTask:
    """Apply an accepted edit, activating continuous coverage exactly once.

    Detail PUT/PATCH and the named rerun action (also used by MCP) share this
    write path through EvalTaskSerializer. The caller has authorized the task
    and validated the fields and eval configs. Lifecycle-only writers must use
    update_fields rather than saving a stale copy of its configuration.
    """
    using = router.db_for_write(EvalTask, instance=task)
    with transaction.atomic(using=using):
        # Use the writer's locked state, not an instance fetched before another
        # edit committed or before reconciliation advanced the cursor.
        current = (
            EvalTask.no_workspace_objects.using(using)
            .select_for_update()
            .get(pk=task.pk)
        )
        updates = dict(changes)
        evals = updates.pop("evals", None)
        run_type = updates.get("run_type", current.run_type)
        if run_type == RunType.CONTINUOUS:
            now = timezone.now()
            if current.run_type != RunType.CONTINUOUS:
                # A historical execution timestamp is not continuous coverage.
                floor = updates["start_time"] = now
                updates["continuous_cursor"] = None
            else:
                # An ordinary edit/retry must not replace the activation floor,
                # including when a detail PUT echoes start_time. It only raises
                # a floor that a row saved before floors were persisted left
                # behind its last acceptance.
                floor = continuous_coverage_floor(current, now=now)
                stored = current.start_time or current.created_at
                updates["start_time"] = current.start_time if floor == stored else floor
            if updates.get("continuous_cursor", current.continuous_cursor) is None:
                # Until a reconcile parks a cursor, last_run is the latest
                # acceptance. An edit that keeps the floor must not move it
                # past the floor: the next start would take the edit for a new
                # acceptance and skip every arrival since the real one. A rerun
                # that resets the cursor stamps both afterwards.
                last_run = updates.get("last_run", current.last_run)
                if last_run is not None and last_run > floor:
                    updates["last_run"] = (
                        None
                        if current.last_run is None
                        else min(current.last_run, floor)
                    )
        for field, value in updates.items():
            setattr(current, field, value)
        current.save(using=using)
        if evals is not None:
            current.evals.set(evals)
        return current
