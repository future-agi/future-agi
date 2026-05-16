export const SYSTEM_METRIC_COLUMN_ALIASES = Object.freeze({
  latency: "latency_ms",
  avg_latency: "latency_ms",
  avg_latency_ms: "latency_ms",
  avg_cost: "cost",
  tokens: "total_tokens",
  input_tokens: "prompt_tokens",
  output_tokens: "completion_tokens",
});

const SYSTEM_METRIC_COL_TYPES = new Set([
  "SYSTEM_METRIC",
  "system_metric",
  "system",
]);

const getFilterColType = (filter) =>
  filter?.filter_config?.col_type ?? filter?.col_type;

export const canonicalizeSystemMetricColumnId = (columnId, colType) => {
  if (!columnId) return columnId;
  if (colType && !SYSTEM_METRIC_COL_TYPES.has(String(colType))) {
    return columnId;
  }
  return SYSTEM_METRIC_COLUMN_ALIASES[columnId] || columnId;
};

export const canonicalizeApiFilterColumnIds = (filters) => {
  if (!Array.isArray(filters)) return filters;

  return filters.map((filter) => {
    if (!filter || typeof filter !== "object") return filter;

    const columnId = filter.column_id;
    const canonicalColumnId = canonicalizeSystemMetricColumnId(
      columnId,
      getFilterColType(filter),
    );

    return canonicalColumnId === columnId
      ? filter
      : { ...filter, column_id: canonicalColumnId };
  });
};
