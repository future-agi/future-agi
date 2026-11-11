// "Completed" is the backend's true status even when most evaluation runs
// errored or were skipped, so the header says what the finished run produced
// next to it. Counts come only from a finished run's own log summary;
// anything else (loading, failed, still running, malformed) shows nothing,
// never 0. `empty` marks a run with no evaluation runs at all, whose logs
// have nothing to show.
export function taskRunOutcome(logs) {
  if (!logs || logs.status !== "completed") return null;
  const {
    success_count: success,
    errors_count: errors,
    skipped_count: skipped,
    total_count: total,
  } = logs;
  if (![success, errors, skipped, total].every(Number.isSafeInteger))
    return null;
  const count = (value) => value.toLocaleString();
  if (total === 0)
    return { role: "warning", label: "Nothing was evaluated", empty: true };
  if (success === 0 && skipped === total)
    return {
      role: "warning",
      label:
        total === 1
          ? "Nothing was evaluated: 1 evaluation run skipped"
          : `Nothing was evaluated: all ${count(total)} evaluation runs skipped`,
    };
  if (errors <= 0 && skipped <= 0) return null;
  // Danger only when every run errored; skipped runs never ran at all.
  if (errors >= total)
    return {
      role: "danger",
      label: `${count(errors)} of ${count(total)} evaluation runs failed`,
    };
  if (errors > 0 && skipped > 0)
    return {
      role: "warning",
      label: `${count(errors)} failed · ${count(skipped)} skipped of ${count(total)} evaluation runs`,
    };
  return errors > 0
    ? {
        role: "warning",
        label: `${count(errors)} of ${count(total)} evaluation runs failed`,
      }
    : {
        role: "warning",
        label: `${count(skipped)} of ${count(total)} evaluation runs skipped`,
      };
}
