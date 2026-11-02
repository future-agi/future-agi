// Widget editor constants shared by the Observe dashboards editor and the
// Simulate run analytics widget editor, so both offer the same chart types,
// icons and series colours.

export const AXIS_LABEL_MAX_LENGTH = 50;

export const CHART_TYPES = [
  { label: "Line", value: "line", icon: "mdi:chart-line", group: "line" },
  {
    label: "Stacked Line",
    value: "stacked_line",
    icon: "mdi:chart-line-stacked",
    group: "line",
  },
  { label: "Column", value: "column", icon: "mdi:chart-bar", group: "column" },
  {
    label: "Stacked Column",
    value: "stacked_column",
    icon: "mdi:chart-bar-stacked",
    group: "column",
  },
  {
    label: "Bar",
    value: "bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  {
    label: "Stacked Bar",
    value: "stacked_bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  { label: "Pie", value: "pie", icon: "mdi:chart-pie", group: "other" },
  { label: "Table", value: "table", icon: "mdi:table", group: "other" },
  { label: "Metric", value: "metric", icon: "mdi:pound", group: "other" },
];

// Additional aggregation options for dataset-specific types
export const DATASET_EXTRA_AGGREGATIONS = [
  { label: "Pass Rate", value: "pass_rate" },
  { label: "Fail Rate", value: "fail_rate" },
  { label: "Pass Count", value: "pass_count" },
  { label: "Fail Count", value: "fail_count" },
  { label: "True Rate", value: "true_rate" },
];

export const METRIC_TYPE_ICONS = {
  system: "mdi:cog-outline",
  eval_metric: "mdi:check-circle-outline",
  annotation: "mdi:format-quote-close",
  custom_attribute: "mdi:tune-variant",
  custom_column: "mdi:table-column",
};

export const SERIES_COLORS = [
  "#7B56DB", // purple (primary)
  "#1ABCFE", // cyan
  "#FF6B6B", // coral red
  "#2ECB71", // emerald green
  "#F7B731", // amber
  "#E84393", // magenta pink
  "#0984E3", // ocean blue
  "#FD7E14", // tangerine orange
  "#00CEC9", // teal
  "#A29BFE", // lavender
];

const hashSeriesName = (name) => {
  const s = String(name || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};
export const buildSeriesColorMap = (names) => {
  const map = {};
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % SERIES_COLORS.length;
    let picked = start;
    for (let i = 0; i < SERIES_COLORS.length; i += 1) {
      const candidate = (start + i) % SERIES_COLORS.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = SERIES_COLORS[picked];
  });
  return map;
};
export const getSeriesColor = (name, map) =>
  (map && map[name]) ||
  SERIES_COLORS[hashSeriesName(name) % SERIES_COLORS.length];

export const LETTER_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
