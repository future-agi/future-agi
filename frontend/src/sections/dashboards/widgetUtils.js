export const DEFAULT_DECIMALS = 2;

export const escapeHtml = (str) => {
  if (typeof str !== "string") return str;
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

export const getSeriesAverage = (points = []) => {
  let total = 0;
  let count = 0;
  for (const pt of points) {
    if (pt?.y == null) continue;
    const y = Number(pt.y);
    if (!Number.isFinite(y)) continue;
    total += y;
    count += 1;
  }
  return count > 0 ? total / count : null;
};

export const getAutoDecimals = (series = []) => {
  let minAbs = Infinity;
  for (const s of series) {
    for (const pt of s.data || []) {
      const raw = typeof pt === "number" ? pt : pt?.y;
      const value = Number(raw);
      if (!Number.isFinite(value)) continue;
      const abs = Math.abs(value);
      if (abs > 0 && abs < minAbs) minAbs = abs;
    }
  }
  if (minAbs === Infinity || minAbs >= 0.01) return DEFAULT_DECIMALS;
  if (minAbs >= 0.001) return 3;
  return 4;
};

const UNIT_LESS_AGGREGATIONS = new Set([
  "count",
  "count_distinct",
  "pass_count",
  "fail_count",
]);

const UNIT_RENDERING = {
  $: { prefixSuffix: "prefix" },
  "%": { prefixSuffix: "suffix" },
  "#": { prefixSuffix: "prefix" },
  ms: { prefixSuffix: "suffix", separator: " " },
  s: { prefixSuffix: "suffix", separator: " " },
  cents: { prefixSuffix: "suffix", separator: " " },
  tokens: { prefixSuffix: "suffix", separator: " " },
  wpm: { prefixSuffix: "suffix", separator: " " },
  "/min": { prefixSuffix: "suffix" },
};

export const getUnitRendering = (unit) => {
  if (!unit) return { unit: "", prefixSuffix: "prefix" };
  const r = UNIT_RENDERING[unit];
  return r ? { unit, ...r } : { unit, prefixSuffix: "suffix", separator: " " };
};

export const getSuggestedUnitConfig = (metricConfigs = []) => {
  if (
    metricConfigs.some((metric) =>
      UNIT_LESS_AGGREGATIONS.has(metric?.aggregation),
    )
  ) {
    return { unit: "", prefixSuffix: "prefix" };
  }
  const allUnits = metricConfigs.map((metric) => metric?.unit ?? "");
  const uniqueUnits = [...new Set(allUnits)];
  if (uniqueUnits.length !== 1 || !uniqueUnits[0]) {
    return { unit: "", prefixSuffix: "prefix" };
  }
  const [unit] = uniqueUnits;
  const rendering = UNIT_RENDERING[unit];
  if (rendering) return { unit, ...rendering };
  return { unit: "", prefixSuffix: "prefix" };
};

export const getAggColumnLabel = (metrics, allAggregations) => {
  if (!metrics?.length) return "Average";
  const uniqueAggs = [...new Set(metrics.map((m) => m.aggregation || "avg"))];
  if (uniqueAggs.length === 1) {
    return (
      allAggregations.find((a) => a.value === uniqueAggs[0])?.label ?? "Average"
    );
  }
  return "Agg.";
};

// True if any series entry has at least one data point.
export const seriesHasDataPoints = (series = []) =>
  series.some((s) => (s?.data || []).length > 0);

// ApexCharts silently clips any series point outside yaxis min/max — if
// every point in every series falls outside the configured bounds, the
// chart renders fully blank with no indication why. Surface that as a
// message instead of an empty canvas.
export const getYAxisRangeWarning = (series = [], axisConfig = {}) => {
  const rightCfg = axisConfig?.rightY || {};
  const seriesAxis = axisConfig?.seriesAxis || {};
  const hasRightAxis =
    rightCfg.visible && Object.values(seriesAxis).some((s) => s === "right");
  if (hasRightAxis) return null;

  const leftAxisConfig = axisConfig?.leftY || {};
  const parseBound = (value) => {
    if (value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const min = parseBound(leftAxisConfig.min);
  const max = parseBound(leftAxisConfig.max);
  if (min == null && max == null) return null;

  let sawPoint = false;
  for (const s of series) {
    for (const pt of s.data || []) {
      if (pt?.y == null) continue;
      const y = Number(pt.y);
      if (!Number.isFinite(y)) continue;
      sawPoint = true;
      if ((min == null || y >= min) && (max == null || y <= max)) {
        return null;
      }
    }
  }
  if (!sawPoint) return null;

  if (min != null && max != null) {
    return `Data is outside your configured Y-axis range (${min}–${max}). Adjust bounds to see your data.`;
  }
  if (min != null) {
    return `Data is outside your configured Y-axis minimum (${min}). Adjust bounds to see your data.`;
  }
  return `Data is outside your configured Y-axis maximum (${max}). Adjust bounds to see your data.`;
};

export const formatValueWithConfig = (
  val,
  cfg,
  { fallbackDecimals = DEFAULT_DECIMALS, includeUnit = true } = {},
) => {
  if (val == null) return "-";
  const num = Number(val);
  if (!Number.isFinite(num)) return "-";
  const dec = Math.max(0, Math.min(6, cfg?.decimals ?? fallbackDecimals));
  const unit = includeUnit ? cfg?.unit || "" : "";
  const prefixSuffix = cfg?.prefixSuffix || "prefix";
  let str;
  if (Boolean(cfg?.abbreviation ?? true) && Math.abs(num) >= 1000000) {
    str = `${(num / 1000000).toFixed(dec)}M`;
  } else if (Boolean(cfg?.abbreviation ?? true) && Math.abs(num) >= 1000) {
    str = `${(num / 1000).toFixed(dec)}K`;
  } else {
    str = num.toFixed(dec);
  }
  if (!unit) return str;
  const rendering = UNIT_RENDERING[unit] || {};
  const separator = rendering.separator ?? "";
  return prefixSuffix === "suffix"
    ? `${str}${separator}${unit}`
    : `${unit}${separator}${str}`;
};
