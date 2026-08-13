export const DATE_PRESETS = [
  { label: "Custom", value: "custom" },
  { label: "30 mins", value: "30m" },
  { label: "6 hrs", value: "6h" },
  { label: "Today", value: "today" },
  { label: "Yesterday", value: "yesterday" },
  { label: "7D", value: "7D" },
  { label: "30D", value: "30D" },
  { label: "3M", value: "3M" },
  { label: "6M", value: "6M" },
  { label: "12M", value: "12M" },
];

export const WIDTH_OPTIONS = [
  { label: "1/4 width", value: 3, icon: "mdi:view-column-outline" },
  { label: "1/3 width", value: 4, icon: "mdi:view-column-outline" },
  { label: "1/2 width", value: 6, icon: "mdi:view-split-vertical" },
  { label: "Full width", value: 12, icon: "mdi:view-sequential-outline" },
];

export const MIN_WIDGET_HEIGHT = 120;
export const DEFAULT_WIDGET_HEIGHT = 320;

export const AGGREGATION_OPTIONS = [
  { label: "Sum", value: "sum" },
  { label: "Average", value: "avg" },
  { label: "Median", value: "median" },
  { label: "Distinct Count", value: "count_distinct" },
  { label: "Count", value: "count" },
  { label: "Minimum", value: "min" },
  { label: "Maximum", value: "max" },
];

export const PERCENTILE_OPTIONS = [
  { label: "25th Percentile", value: "p25" },
  { label: "50th Percentile", value: "p50" },
  { label: "75th Percentile", value: "p75" },
  { label: "90th Percentile", value: "p90" },
  { label: "95th Percentile", value: "p95" },
  { label: "99th Percentile", value: "p99" },
];

export const ALL_AGGREGATIONS = [...AGGREGATION_OPTIONS, ...PERCENTILE_OPTIONS];

// Shared style for the date-filter chips (font from theme, not hardcoded).
export const DATE_CHIP_SX = {
  typography: "caption",
  fontWeight: "fontWeightMedium",
  height: 28,
  borderRadius: "6px",
};

export const AVATAR_COLORS = [
  "#7C4DFF",
  "#FF6B6B",
  "#5BE49B",
  "#FFB547",
  "#36B5FF",
  "#FF85C0",
  "#00BFA6",
  "#8C9EFF",
];

/**
 * Grid track definition for the dashboards list.
 *
 * The header row and every data row must render with the same tracks, so both
 * read this single definition. `DASHBOARD_LIST_COLUMNS` includes the trailing
 * 32px row-action track; `DASHBOARD_LIST_CONTENT_COLUMNS` covers the metadata
 * columns only, for contexts that render without the action column.
 */
export const DASHBOARD_LIST_COLUMNS =
  "minmax(220px, 1fr) 96px 112px minmax(160px, 220px) 88px 32px";

export const DASHBOARD_LIST_CONTENT_COLUMNS =
  "minmax(220px, 1fr) 96px 112px minmax(160px, 220px)";

/**
 * Standard visually-hidden box.
 *
 * Values are explicit CSS strings on purpose: MUI's `sx` runs bare numbers
 * through its sizing transform (any number <= 1 becomes a percentage) and its
 * spacing transform (`margin: -1` becomes -8px), which would turn this into a
 * full-size overlay rather than the intended 1px clip box.
 */
export const VISUALLY_HIDDEN_SX = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};
