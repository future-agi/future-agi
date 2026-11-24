"""A write fence for the first historical sampling pass after its timer."""

from tracer.models.eval_task import EvalTask, EvalTaskStatus


class InactiveTask(Exception):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def locked_running_task(task_id) -> EvalTask:
    """Call inside the mutation transaction. A soft delete need not set status."""
    task = EvalTask.all_objects.select_for_update().get(pk=task_id)
    # Detail PATCH can restore PENDING without dispatching a replacement workflow.
    if task.deleted or task.status in {
        EvalTaskStatus.PAUSED,
        EvalTaskStatus.DELETED,
        EvalTaskStatus.COMPLETED,
        EvalTaskStatus.FAILED,
    }:
        raise InactiveTask(
            EvalTaskStatus.DELETED if task.deleted else task.status or ""
        )
    return task
