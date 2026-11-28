export const USAGE_SORT_COLUMNS = [
  "cost",
  "total_tokens",
  "prompt_tokens",
  "completion_tokens",
];

// TraceObserveListQuerySerializer deliberately rejects global usage ordering
// for cross-project, session, attribute and evaluator predicates. Keep those
// existing filter journeys usable instead of sending an unsupported sort.
export function supportsUsageSort(projectId, filters) {
  return (
    Boolean(projectId) &&
    filters.every((item) => {
      const config = item.filter_config || item.filterConfig || {};
      const column = item.column_id || item.columnId;
      const family = config.col_type || config.colType || "NORMAL";
      return (
        ["NORMAL", "SYSTEM_METRIC"].includes(family) &&
        [...USAGE_SORT_COLUMNS, "created_at", "start_time"].includes(column)
      );
    })
  );
}

// Only the unedited evidence link gets a numbered global latency order. Never
// promote the latency column to a sortable grid header or inherit this order
// for the session, comparison, or arbitrary status/eval/attribute filters.
export function supportsRuntimeLatencySort(projectId, sort, filters) {
  if (!projectId || sort?.column_id !== "latency" || sort.direction !== "desc" || filters.length !== 2) return false;
  const plainLeaf = (f) => Object.keys(f).every(key => ["column_id", "filter_config"].includes(key)) &&
    Object.keys(f.filter_config || {}).every(key => ["col_type", "filter_type", "filter_op", "filter_value"].includes(key));
  if (!filters.every(plainLeaf)) return false;
  const cohort = filters.find(f => f.column_id === "skill.trace_cohort")?.filter_config;
  const date = filters.find(f => f.column_id === "created_at")?.filter_config;
  return cohort?.col_type === "SKILL" && cohort.filter_type === "text" && cohort.filter_op === "equals" &&
    typeof cohort.filter_value === "string" && /^[a-f0-9]{64}$/.test(cohort.filter_value) &&
    (date?.col_type ?? "NORMAL") === "NORMAL" && date?.filter_type === "datetime" && date.filter_op === "between" &&
    Array.isArray(date.filter_value) && date.filter_value.length === 2;
}
