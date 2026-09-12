export const DEFAULT_COLUMN_SUMMARY_TYPE = "average";

export const COLUMN_SUMMARY_TYPES = [
  { id: "average", label: "Average" },
  { id: "max", label: "Maximum" },
  { id: "min", label: "Minimum" },
  { id: "median", label: "Median" },
];

export const getColumnSummaryLabel = (type) =>
  COLUMN_SUMMARY_TYPES.find((item) => item.id === type)?.label ?? "Average";

export const getColumnSummaryStats = (col) => {
  if (!col) return null;
  const average = col.average_score ?? col.averageScore;
  if (average === null || average === undefined) return null;
  return {
    isColumnSummary: true,
    average,
    max: col.max_score ?? col.maxScore ?? null,
    min: col.min_score ?? col.minScore ?? null,
    median: col.median_score ?? col.medianScore ?? null,
  };
};

export const formatColumnSummaryValue = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return null;
  }
  return `${Number(value).toFixed(2)}%`;
};

export const resolveColumnSummaryType = (
  stats,
  type = DEFAULT_COLUMN_SUMMARY_TYPE,
) => {
  if (stats && stats[type] !== null && stats[type] !== undefined) {
    return type;
  }
  return DEFAULT_COLUMN_SUMMARY_TYPE;
};

export const formatColumnSummary = (
  stats,
  type = DEFAULT_COLUMN_SUMMARY_TYPE,
) => {
  if (!stats) return null;
  const resolvedType = resolveColumnSummaryType(stats, type);
  const formatted = formatColumnSummaryValue(stats[resolvedType]);
  if (formatted == null) return null;
  return `${getColumnSummaryLabel(resolvedType)}: ${formatted}`;
};

export const getAvailableColumnSummaryTypes = (stats) => {
  if (!stats) return [];
  return COLUMN_SUMMARY_TYPES.filter(
    (item) => stats[item.id] !== null && stats[item.id] !== undefined,
  );
};

export const buildExperimentPinnedSummaryRow = (columnMap) => {
  if (!columnMap?.length) return [];

  const summarisableColumns = columnMap.flatMap((col) =>
    col.children
      ? col.children.filter((child) => getColumnSummaryStats(child?.col))
      : getColumnSummaryStats(col?.col)
        ? [col]
        : [],
  );

  if (summarisableColumns.length === 0) return [];

  const pinnedRow = {};
  summarisableColumns.forEach((col) => {
    if (!col.field) return;
    const stats = getColumnSummaryStats(col.col);
    if (stats) {
      pinnedRow[col.field] = stats;
    }
  });

  return [pinnedRow];
};
