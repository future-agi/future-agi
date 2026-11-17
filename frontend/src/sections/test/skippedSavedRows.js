// A run started on saved dataset rows skips the rows that no longer exist.
// The run-start answer and the run's KPIs carry how many it skipped.

// The skipped row count in a run-start answer (bare or inside `result`) or in
// a run's KPIs; 0 when there is none.
export function skippedSavedRowCount(source) {
  const count =
    source?.result?.skipped_dataset_row_count ??
    source?.skipped_dataset_row_count;
  return Number.isInteger(count) && count > 0 ? count : 0;
}

// The sentence for skipped saved rows, or null when none was skipped.
export function skippedSavedRowsMessage(count) {
  if (!Number.isInteger(count) || count <= 0) return null;
  return count === 1
    ? "1 saved row no longer exists and was skipped."
    : `${count} saved rows no longer exist and were skipped.`;
}
