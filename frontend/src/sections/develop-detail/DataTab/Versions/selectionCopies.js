/**
 * v2.1 g8 (grid:selection-actions): the Data grid's row-copy actions. Pure.
 *
 * Duplicate is a pending change like Add row, Paste and Delete: the server
 * answers the copies as add_row operations (`pending: true`) and these become
 * change-set ops with their own temp ids; Save version keeps them, Undo
 * drops them.
 */
export function duplicateOps(operations = [], stamp = "") {
  return (operations ?? [])
    .filter((op) => op?.op === "add_row")
    .map((op, i) => ({
      op: "add_row",
      temp_id: `new-dup-${stamp}${i + 1}`,
      cells: { ...(op.cells ?? op.cells_by_column_id ?? {}) },
      split_ids: [...(op.split_ids ?? [])],
      ...(op.metadata ? { metadata: op.metadata } : {}),
      ...(op.source ? { source: op.source } : {}),
    }));
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function duplicateToast({
  rows_copied: copied,
  rows_added: added,
} = {}) {
  const n = Number(copied) || Number(added) || 0;
  const copies = n ? Math.round((Number(added) || n) / n) : 1;
  return `Duplicated ${plural(n, "row")}${copies > 1 ? ` ×${copies}` : ""} · unsaved until you save a version`;
}

/**
 * Add to dataset: rows copy into the other dataset by column name. The toast
 * says how many rows went where and which columns did not match, so a
 * mismatch never passes silently.
 */
export function mergeToast(result = {}, fallbackName = "the dataset") {
  const n = Number(result.rows_added) || 0;
  const target = result.target_name ?? fallbackName;
  const parts = [
    `Added ${plural(n, "row")} to ${target}${result.version_number ? ` (saved as v${result.version_number})` : ""}`,
  ];
  const unmatched = result.unmatched_columns ?? [];
  const empty = result.empty_columns ?? [];
  if (unmatched.length)
    parts.push(`not copied (no column of that name): ${unmatched.join(", ")}`);
  if (empty.length) parts.push(`left empty: ${empty.join(", ")}`);
  return parts.join(" · ");
}

export function newDatasetToast(rows, name) {
  const n = Number(rows) || 0;
  return n
    ? `Created ${name ?? "the dataset"} with ${plural(n, "row")}`
    : `Created ${name ?? "the dataset"}`;
}
