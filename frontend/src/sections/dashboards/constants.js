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
