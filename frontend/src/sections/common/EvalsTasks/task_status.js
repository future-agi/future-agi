// Statuses the backend's unpause_eval_task and the AI tool accept. Keep in sync
// with `RESUMABLE_TASK_STATUSES` in tracer/models/eval_task.py: a failed task
// leaves its entries untouched just like a paused one, so Resume drains what is
// left.
export const RESUMABLE_TASK_STATUSES = new Set(["paused", "failed"]);

export const isResumableTaskStatus = (status) =>
  RESUMABLE_TASK_STATUSES.has((status || "").toLowerCase());
