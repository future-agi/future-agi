import { getExactAggregationReadState } from "src/utils/queryReadState";

export const DEFAULT_DECIMALS = 2;

export const getDashboardMetricSeriesState = (metrics = []) => {
  const metricReadStates = (Array.isArray(metrics) ? metrics : []).map(
    (metric) => ({
      metric,
      readState: getExactAggregationReadState(metric),
    }),
  );
  const hasSampledMetrics = metricReadStates.some(
    ({ readState }) => readState === "sampled",
  );
  const hasDegradedMetrics = metricReadStates.some(
    ({ readState }) => readState === "degraded" || readState === "error",
  );
  const hasPendingMetrics = metricReadStates.some(
    ({ readState }) => readState === "pending",
  );
  const hasUnavailableMetrics =
    hasSampledMetrics || hasDegradedMetrics || hasPendingMetrics;
  const renderableMetrics = hasUnavailableMetrics
    ? []
    : metricReadStates.filter(({ readState }) => readState === "complete");
  const series = [];

  for (const [metricIndex, { metric }] of renderableMetrics.entries()) {
    for (const metricSeries of metric.series || []) {
      const isSingleMetric = renderableMetrics.length === 1;
      let name;
      if (metricSeries.name === "total") {
        name = `${metric.name} (${metric.aggregation})`;
      } else if (isSingleMetric) {
        name = metricSeries.name;
      } else {
        name = `${metric.name} / ${metricSeries.name} (${metric.aggregation})`;
      }
      series.push({
        name,
        key: makeSeriesKey(metric, metricSeries.name),
        metricIndex,
        metricName: metric.name,
        aggregation: metric.aggregation,
        unit: metric.unit ?? "",
        breakdownName: metricSeries.name,
        data: (metricSeries.data || []).map((point) => ({
          x: new Date(point.timestamp).getTime(),
          y: point.value != null ? Number(point.value) : null,
        })),
      });
    }
  }

  return {
    metricReadStates,
    renderableMetrics,
    series,
    hasSampledMetrics,
    hasDegradedMetrics,
    hasPendingMetrics,
  };
};

/**
 * A missing aggregate bucket is not a zero. For line charts, omit null points
 * so Apex connects the neighbouring observed points without mutating the exact
 * response used by table and non-line renderers.
 */
export const getPlottedChartSeries = (series = [], isLineChart = false) =>
  isLineChart
    ? series.map((item) => ({
        ...item,
        data: (item?.data || []).filter((point) => point?.y != null),
      }))
    : series;

export const shouldConnectAcrossMissingBuckets = (apexType) =>
  apexType === "line" || apexType === "area";

/**
 * Dashboard responses are all-or-nothing aggregates. A single sampled,
 * degraded, or failed metric makes the payload non-renderable.
 */
export const getExactDashboardResult = (payload) => {
  if (!payload || getExactAggregationReadState(payload) !== "complete") {
    return null;
  }

  const result = payload?.data?.result ?? payload?.result;
  if (!result || !Array.isArray(result.metrics)) return null;
  const metricState = getDashboardMetricSeriesState(result.metrics);
  return metricState.hasSampledMetrics ||
    metricState.hasDegradedMetrics ||
    metricState.hasPendingMetrics
    ? null
    : result;
};

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
/**
 * Bounds that fit the data, never null unless there is genuinely nothing to
 * scale. Prefers the zero-anchored result; where that is declined (a narrow
 * band well above zero, or one that dips below it) it fits the band instead,
 * snapping the floor onto the step grid so tick labels stay round.
 *
 * Every axis goes through this. Dual-axis is the case that *requires* it —
 * every entry on a side must carry the same explicit bounds or ApexCharts
 * scales each series on its own, which draws a small series as though it
 * filled the plot. Single-axis wants it for the narrow-band case, where the
 * alternative is ApexCharts' coarse {1,2,5,10} step ladder and the dead space
 * that comes with it.
 *
 * Null still comes back where there is no band to fit: a logarithmic side,
 * one with fewer than two finite points, or one whose points are all equal.
 * Those keep ApexCharts' own scaling, so the invariant above is not absolute.
 */
export const getFittedYAxisBounds = (
  series = [],
  { stacked = false, logarithmic = false, tickAmount = 5 } = {},
) => {
  if (logarithmic) return null;
  const zeroAnchored = getAutoYAxisBounds(series, {
    stacked,
    logarithmic,
    tickAmount,
  });
  if (zeroAnchored) return zeroAnchored;

  const extent = getSeriesExtent(series, { stacked });
  if (!extent) return null;
  const span = extent.max - extent.min;
  if (span <= 0) return null;

  // Flooring the min onto the step grid consumes up to a full step, and the
  // max is measured from that lowered floor — so a step sized off the raw span
  // alone can land below the peak, which ApexCharts then clips. Grow the step
  // until the floored grid still reaches the peak.
  let step = niceCeil(span / tickAmount);
  let min = Math.floor(extent.min / step) * step;
  while (min + step * tickAmount < extent.max) {
    step = niceCeil(
      step + (extent.max - (min + step * tickAmount)) / tickAmount,
    );
    min = Math.floor(extent.min / step) * step;
  }
  return { min: normalize(min), max: normalize(min + step * tickAmount) };
};

/**
 * Final {min, max} for one axis, given the series plotted against it.
 *
 * A typed Threshold Bound is used as given; a side left empty is auto-scaled.
 * With "Out of Bounds: Visible" a typed bound that would push data off the
 * chart is widened so every point stays visible; "Hidden" keeps it as a hard
 * cap and clips. Either value may come back undefined, meaning "say nothing
 * and let ApexCharts decide".
 *
 * Pass only the series belonging to this axis. On a dual-axis chart every
 * entry for a side must be given the same result, or ApexCharts scales each
 * series independently and a small series is stretched to fill the plot.
 */
export const resolveAxisBounds = (
  series = [],
  cfg = {},
  { stacked = false, tickAmount = 5, fit = false } = {},
) => {
  const compute = fit ? getFittedYAxisBounds : getAutoYAxisBounds;
  const auto = compute(series, {
    stacked,
    logarithmic: cfg.scale === "logarithmic",
    tickAmount,
  });
  const extent = getSeriesExtent(series, { stacked });
  const widen = cfg.outOfBounds !== "hidden" && extent;
  const typedMin = parseBound(cfg.min);
  const typedMax = parseBound(cfg.max);
  const userMin =
    widen && typedMin != null && typedMin > extent.min ? null : typedMin;
  const userMax =
    widen && typedMax != null && typedMax < extent.max ? null : typedMax;
  return { min: userMin ?? auto?.min, max: userMax ?? auto?.max };
};

/**
 * The y-axis plan for one widget: whether a right axis is actually drawn, which
 * side each drawn series belongs to, and the bounds for each side.
 *
 * The saved widget (WidgetChart) and the editor preview (WidgetEditorView) both
 * build their `yaxis` from this, so the two cannot disagree about scaling. They
 * used to derive it separately, and a fix applied to one could silently miss
 * the other.
 *
 * `chartSeries` is the visible series, `chartSeriesIndices` their original
 * indices — `axisConfig.seriesAxis` is keyed by the unfiltered index, so
 * anything reading it from the filtered list must map back through them.
 */
export const resolveWidgetAxisPlan = (
  chartSeries = [],
  chartSeriesIndices = [],
  axisConfig = {},
  { stacked = false } = {},
) => {
  const leftCfg = axisConfig?.leftY || {};
  const rightCfg = axisConfig?.rightY || {};
  const seriesAxis = axisConfig?.seriesAxis || {};

  // Read off the *visible* series. Hiding the only right-assigned series must
  // drop the chart back to single-axis, or the left axis keeps being scaled by
  // dual-axis rules for an axis that is no longer on screen.
  const hasRightAxis =
    !!rightCfg.visible &&
    chartSeriesIndices.some((idx) => seriesAxis[idx] === "right");

  const sideOf = (i) =>
    hasRightAxis && seriesAxis[chartSeriesIndices[i]] === "right"
      ? "right"
      : "left";

  // fit on both paths. Zero-anchoring still wins wherever the data runs to the
  // floor; fitting only adds the case it declines — a band well above zero,
  // which otherwise falls to ApexCharts' coarse {1,2,5,10} step ladder.
  const opts = { stacked, fit: true };
  const on = (side) => chartSeries.filter((__, i) => sideOf(i) === side);

  return {
    hasRightAxis,
    sideOf,
    bounds: hasRightAxis
      ? {
          left: resolveAxisBounds(on("left"), leftCfg, opts),
          right: resolveAxisBounds(on("right"), rightCfg, opts),
        }
      : { left: resolveAxisBounds(chartSeries, leftCfg, opts) },
  };
};

export const getYAxisRangeWarning = (series = [], axisConfig = {}) => {
  const rightCfg = axisConfig?.rightY || {};
  const seriesAxis = axisConfig?.seriesAxis || {};
  const hasRightAxis =
    rightCfg.visible && Object.values(seriesAxis).some((s) => s === "right");
  if (hasRightAxis) return null;

  const leftAxisConfig = axisConfig?.leftY || {};
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

// A bound counts as user-set only when it parses to a finite number. The
// Threshold Bounds inputs are untyped text, so "abc" must read as unset rather
// than reaching ApexCharts as NaN.
export const parseBound = (value) => {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

// Mantissas for the axis step. Finer than the {1,2,5,10} table ApexCharts uses
// internally (settings/Globals.js niceScaleAllowedMagMsd), which is what leaves
// the dead space this helper exists to remove: a 7,043 peak needs a step of
// 1,408.6, which that table rounds to 2,000 and so an axis max of 10,000.
const STEP_MANTISSAS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];

// Round to 12 significant digits before the ladder lookup. Without it,
// 0.3 / 10 ** Math.floor(Math.log10(0.3)) is 2.9999999999999996 and picks the
// rung above the right one — which sub-1 metrics (rates, cost per call) hit
// constantly.
const normalize = (n) => Number(n.toPrecision(12));

const niceCeil = (value) => {
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const mantissa = normalize(value / magnitude);
  const rung = STEP_MANTISSAS.find((m) => m >= mantissa) ?? 10;
  return normalize(rung * magnitude);
};

/**
 * Lowest and highest value the chart actually plots, or null if there is
 * nothing finite to measure. Stacked charts are read off the summed height.
 */
export const getSeriesExtent = (series = [], { stacked = false } = {}) => {
  const totals = [];
  if (stacked) {
    // The backend pads every bucket (null for gaps) so series are aligned and
    // equal-length — the same positional sum ApexCharts itself does.
    const byIndex = [];
    for (const s of series) {
      (s?.data || []).forEach((pt, i) => {
        const value = Number(typeof pt === "number" ? pt : pt?.y);
        if (!Number.isFinite(value)) return;
        byIndex[i] = (byIndex[i] || 0) + value;
      });
    }
    totals.push(...byIndex.filter((v) => Number.isFinite(v)));
  } else {
    for (const s of series) {
      for (const pt of s?.data || []) {
        const value = Number(typeof pt === "number" ? pt : pt?.y);
        if (Number.isFinite(value)) totals.push(value);
      }
    }
  }

  if (totals.length < 2) return null;
  return { min: Math.min(...totals), max: Math.max(...totals) };
};

/**
 * Zero-anchored axis bounds sized to the data, or null to leave ApexCharts alone.
 *
 * Derives the step first and multiplies up (max = step * tickAmount) so tick
 * labels stay round, rather than rounding the max onto a coarse ladder.
 *
 * Returns null whenever zero-anchoring would be wrong or unsafe, most
 * importantly for a narrow band sitting well above zero (40M-60M), where
 * forcing 0 would waste *more* space than it saves. Null is a deferral, not a
 * verdict: callers pass it to getFittedYAxisBounds, which fits the band where
 * it actually sits.
 */
export const getAutoYAxisBounds = (
  series = [],
  { stacked = false, logarithmic = false, tickAmount = 5 } = {},
) => {
  if (logarithmic) return null;

  const extent = getSeriesExtent(series, { stacked });
  if (!extent) return null;

  const { max: peak, min: floor } = extent;
  if (floor < 0) return null;
  if (peak <= 0) return null;

  // Only act where the data already runs most of the way to zero. Above that
  // the series is a narrow high band and zero-anchoring is a regression.
  if (floor > 0.3 * peak) return null;

  const step = niceCeil(peak / tickAmount);
  const max = normalize(step * tickAmount);

  // max === peak is left alone deliberately: it is a perfect fit. Nudging it to
  // clear the topmost marker would mean either an off-ladder max (0/48/96/...
  // instead of 0/40/80/...) or a whole extra rung, which on a 5,000 peak means
  // a 7,500 axis — reintroducing the dead space this exists to remove.
  return { min: 0, max };
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

/**
 * Original indices of the currently visible series, in ascending order — the
 * same order `series.filter((_, i) => visibleSeries.has(i))` produces.
 *
 * `axis_config.series_axis` is keyed by the index in the UNFILTERED series
 * list, so anything reading it from the filtered chart series must map back
 * through this. Reading it with the filtered index silently reassigns axes as
 * soon as a series is hidden, and spreading the Set (`[...visibleSeries][i]`)
 * is wrong too: it iterates in insertion order, which for a top-N selection is
 * rank order, not index order.
 */
export const getVisibleIndices = (series = [], visibleSeries = null) => {
  const all = series.map((_, i) => i);
  return visibleSeries === null ? all : all.filter((i) => visibleSeries.has(i));
};

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
