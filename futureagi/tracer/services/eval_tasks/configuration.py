"""Persist validated evaluation-task edits and their coverage boundary."""

from django.db import router, transaction
from django.utils import timezone

from tracer.models.eval_task import EvalTask, RunType


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
            if current.run_type != RunType.CONTINUOUS:
                # A historical execution timestamp is not continuous coverage.
                updates["start_time"] = timezone.now()
                updates["continuous_cursor"] = None
            else:
                # An ordinary edit/retry must not replace the activation floor,
                # including when a detail PUT echoes start_time.
                updates["start_time"] = current.start_time
        for field, value in updates.items():
            setattr(current, field, value)
        current.save(using=using)
        if evals is not None:
            current.evals.set(evals)
        return current
