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
