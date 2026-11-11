// Statuses the backend's unpause_eval_task and the AI tool accept. Keep in sync
// with `RESUMABLE_TASK_STATUSES` in tracer/models/eval_task.py: a failed task
// leaves its entries untouched just like a paused one, so Resume drains what is
// left.
export const RESUMABLE_TASK_STATUSES = new Set(["paused", "failed"]);

export const isResumableTaskStatus = (status) =>
  RESUMABLE_TASK_STATUSES.has((status || "").toLowerCase());

const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

// "Completed" is the task's true status even when its run evaluated nothing,
// so the list says so next to it. Reads a row's `progress` block (dispatched /
// completed / skipped / missing, from progress_block); without one, or with
// any count that is not a whole number, there is no hint, never a 0. Failures
// are not split out of `completed` there, so only empty and skipped runs show.
export function completedTaskHint(status, progress) {
  if ((status || "").toLowerCase() !== "completed" || !progress) return null;
  const { dispatched, skipped } = progress;
  if (!isCount(dispatched) || !isCount(skipped) || skipped > dispatched)
    return null;
  const runs = (count) =>
    `${count.toLocaleString()} evaluation run${count === 1 ? "" : "s"}`;
  if (dispatched === 0)
    return {
      label: "Nothing evaluated",
      title: "The last run created no evaluation runs.",
    };
  if (skipped === 0) return null;
  if (skipped === dispatched && dispatched === 1)
    return {
      label: "1 skipped",
      title: "Nothing was evaluated: the only evaluation run was skipped.",
    };
  if (skipped === dispatched)
    return {
      label: `All ${dispatched.toLocaleString()} skipped`,
      title: `Nothing was evaluated: all ${runs(dispatched)} were skipped.`,
    };
  return {
    label: `${skipped.toLocaleString()} skipped`,
    title: `${skipped.toLocaleString()} of ${runs(dispatched)} skipped.`,
  };
}
