export const DEFAULT_DECIMALS = 2;

const toAxisPayload = ({ prefixSuffix, outOfBounds, ...axis } = {}) => ({
  ...axis,
  ...(prefixSuffix !== undefined && { prefix_suffix: prefixSuffix }),
  ...(outOfBounds !== undefined && { out_of_bounds: outOfBounds }),
});

const fromAxisPayload = ({ prefix_suffix, out_of_bounds, ...axis } = {}) => ({
  ...axis,
  ...(prefix_suffix !== undefined && { prefixSuffix: prefix_suffix }),
  ...(out_of_bounds !== undefined && { outOfBounds: out_of_bounds }),
});

export const toAxisConfigPayload = ({
  leftY,
  rightY,
  xAxis,
  seriesAxis,
  ...config
} = {}) => ({
  ...config,
  ...(leftY !== undefined && { left_y: toAxisPayload(leftY) }),
  ...(rightY !== undefined && { right_y: toAxisPayload(rightY) }),
  ...(xAxis !== undefined && { x_axis: xAxis }),
  ...(seriesAxis !== undefined && { series_axis: seriesAxis }),
});

export const fromAxisConfigPayload = ({
  left_y,
  leftY,
  right_y,
  rightY,
  x_axis,
  xAxis,
  series_axis,
  seriesAxis,
  ...config
} = {}) => ({
  ...config,
  leftY: fromAxisPayload(left_y ?? leftY),
  rightY: fromAxisPayload(right_y ?? rightY),
  xAxis: x_axis ?? xAxis ?? {},
  seriesAxis: series_axis ?? seriesAxis ?? {},
});

export const escapeHtml = (str) => {
  if (typeof str !== "string") return str;
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

// Aggregations whose bucket values recombine exactly by summing. Keep the
// count family complete: the dataset metrics add pass_count/fail_count, and
// averaging a count reports a per-bucket figure as if it were the total.
//
// count_distinct is deliberately absent. The backend evaluates it as
// `uniq({col})` per time bucket, so an entity present in several buckets is
// counted once per bucket and summing multiplies it by the bucket count.
const ADDITIVE_AGGREGATIONS = new Set([
  "sum",
  "count",
  "pass_count",
  "fail_count",
]);

// Whether adding this aggregation's values together yields a real quantity.
// Summing per-slice averages or maxima does not — a pie of avg latency by
// project has no meaningful grand total to print in the middle.
export const isAdditiveAggregation = (aggregation) =>
  ADDITIVE_AGGREGATIONS.has(aggregation);

// Collapse an already-bucketed series to a single scalar, honouring the
// metric's own aggregation. The backend aggregates *within* each time bucket
// and sends no period total, so any single-number view (pie slice, metric
// card, table Agg column) has to recombine the buckets here.
//
// Exact for sum/count/min/max. For avg, median and percentiles this is an
// approximation: recombining correctly needs per-bucket row counts the API
// does not send, so an unweighted mean is the closest available answer.
export const getSeriesScalar = (points = [], aggregation = "avg") => {
  const values = [];
  for (const pt of points) {
    if (pt?.y == null) continue;
    const y = Number(pt.y);
    if (!Number.isFinite(y)) continue;
    values.push(y);
  }
  if (!values.length) return null;
  if (ADDITIVE_AGGREGATIONS.has(aggregation)) {
    return values.reduce((a, b) => a + b, 0);
  }
  if (aggregation === "min") return Math.min(...values);
  if (aggregation === "max") return Math.max(...values);
  return values.reduce((a, b) => a + b, 0) / values.length;
};

// Max slices shown in a single pie, applied per metric rather than across the
// flat series list — a global cap could strip every slice from one metric and
// leave an empty donut.
const MAX_PIE_SLICES = 10;

// Group a flat series list into one pie per metric. Each slice is a breakdown
// value collapsed by that metric's own aggregation, so unrelated metrics are
// never combined into a single donut (TH-6530).
//
// Only slices a ring can draw become slices: a zero or negative value has no
// arc, so keeping it would inflate the legend and the slice count. The metric
// itself is always kept — silently removing one the user added looks like the
// add failed — and `hasValues` lets its panel say whether the data was all
// zero or absent entirely.
export const groupPieSeries = (series = []) => {
  const byMetric = new Map();
  for (const s of series) {
    if (!byMetric.has(s.metricIndex)) {
      byMetric.set(s.metricIndex, {
        metricIndex: s.metricIndex,
        metricName: s.metricName,
        aggregation: s.aggregation,
        unit: s.unit ?? "",
        hasValues: false,
        slices: [],
      });
    }
    const group = byMetric.get(s.metricIndex);
    const value = getSeriesScalar(s.data, s.aggregation);
    if (value == null) continue;
    group.hasValues = true;
    if (value <= 0) continue;
    group.slices.push({ name: s.breakdownName, value });
  }
  return [...byMetric.values()].map((group) => {
    if (group.slices.length <= MAX_PIE_SLICES) return { ...group };

    const ranked = [...group.slices].sort((a, b) => b.value - a.value);

    // Values that do not add up cannot be folded into a remainder without
    // inventing a quantity, so the tail is dropped instead. getCenterValue
    // already refuses to print a total for these.
    if (!isAdditiveAggregation(group.aggregation)) {
      return { ...group, slices: ranked.slice(0, MAX_PIE_SLICES) };
    }

    // Otherwise carry the remainder as one slice. Dropping it would leave the
    // ring normalised over a subset and the centre reporting that subset as
    // the metric's total.
    const kept = ranked.slice(0, MAX_PIE_SLICES - 1);
    const rest = ranked.slice(MAX_PIE_SLICES - 1);
    return {
      ...group,
      slices: [
        ...kept,
        {
          name: `Other (${rest.length})`,
          value: rest.reduce((sum, slice) => sum + slice.value, 0),
        },
      ],
    };
  });
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

// Stable identity for a chart series: metric id + aggregation + raw bucket
// name. Survives metric renames and series reordering, unlike the display label.
export const makeSeriesKey = (metric, bucketName) =>
  `${metric?.id ?? ""}|${metric?.aggregation ?? ""}|${bucketName ?? ""}`;

// Resolve a saved key list to the current series' indices. null => all visible.
export const resolveVisibleSeries = (savedKeys, series) => {
  if (savedKeys === null) return null;
  const keyToIndex = new Map(series.map((s, i) => [s.key, i]));
  return new Set(
    savedKeys.map((k) => keyToIndex.get(k)).filter((i) => i !== undefined),
  );
};

// Decide the visibleSeries state from a saved `visible_series` value, or return
// `undefined` to tell the caller to apply its own default (top-10 / show-all):
//   null            → show all (explicit "Select all")
//   [] (hide all)   → empty Set (explicit)
//   [keys] w/ match → Set of the matched indices (incl. a partial match)
//   [keys] no match → undefined (selection is stale → caller's default)
//   undefined       → undefined (nothing saved → caller's default)
export const resolveSavedSelection = (savedKeys, series) => {
  if (savedKeys === undefined) return undefined;
  const resolved = resolveVisibleSeries(savedKeys, series);
  if (resolved === null || resolved.size > 0) return resolved;
  if (savedKeys.length === 0) return resolved; // intentional hide-all
  return undefined; // stale → caller applies its default
};
