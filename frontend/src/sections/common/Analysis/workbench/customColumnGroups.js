// W5-2 B1 · Explore's group by a custom column (Lane A's result columns).
// The values come only from GET …/result-columns/values/ (the mock's
// w5CustomColumns.customColumnValues), read through useCustomColumns'
// fetchCustomColumnValues. A row column puts the row in the group of its
// value; a run column (an output path) puts each run's attempts on the row
// in the group of that run's own value. Pure.
import { MISSING } from "./workbenchModel";
import { groupNamesOf } from "./matrixScope";

export const CUSTOM_PREFIX = "cc:";

export const isCustomKey = (key) => String(key ?? "").startsWith(CUSTOM_PREFIX);

export const customNameOf = (key) =>
  isCustomKey(key) ? String(key).slice(CUSTOM_PREFIX.length) : null;

/** `Custom column ▸ <name>` options, one per saved column. */
export const customGroupOptions = (columns) =>
  (columns ?? []).map((c) => ({
    key: `${CUSTOM_PREFIX}${c.name}`,
    label: `Custom column ▸ ${c.name}`,
  }));

/** The rows-axis options with the custom columns before `None`. */
export function withCustomGroups(options, columns) {
  const extra = customGroupOptions(columns);
  if (!extra.length) return options;
  const at = options.findIndex((o) => o.key === "none");
  return at < 0
    ? [...options, ...extra]
    : [...options.slice(0, at), ...extra, ...options.slice(at)];
}

/**
 * `(row, runId) → group names` from a values answer
 * (`{columns, values: {example_id: {run_id | "row": {col_id: value}}}}`).
 */
export function customGrouper(result, name) {
  const def = (result?.columns ?? []).find((c) => c.name === name);
  const values = result?.values ?? {};
  return (row, runId) => {
    if (!def) return [MISSING];
    const byRow = values[row.row_id] ?? {};
    const v =
      def.applies_to === "row" ? byRow.row?.[def.id] : byRow[runId]?.[def.id];
    return groupNamesOf(v);
  };
}

/** The column's own error from the values answer (`errors[col_id]`), or null. */
export function customError(result, name) {
  const def = (result?.columns ?? []).find((c) => c.name === name);
  return def ? result?.errors?.[def.id] ?? null : null;
}
