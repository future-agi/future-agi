// HO results R7 (W5 integration B-H2, the metric strip half): the dataset's run
// metrics (W5-4 B2 formulas) as series in the leaderboard strip's Scores
// card. Values come only from GET …/derived-metrics/?runs= (computed by
// w5DerivedMetrics from R1), so the strip and Explore agree. Pure.
import { zoomRange } from "src/sections/develop-detail/ExperimentTab/metricStripModel";
import { fmtMetric } from "./formulaModel";

export const RUN_METRIC_PREFIX = "dm:";
export const runMetricKey = (metric) => `${RUN_METRIC_PREFIX}${metric.id}`;
export const isRunMetricKey = (key) =>
  String(key ?? "").startsWith(RUN_METRIC_PREFIX);

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** A run's value of one metric (`values[run_id][metric_id].value`), or null. */
export const runMetricValue = (values, run, metric) =>
  num(values?.[run?.run_id]?.[metric.id]?.value);

/**
 * One series per run metric over the strip's runs. A percent metric plots on
 * the Scores card's 0–100 axis (value × 100); the others plot their own
 * value on an axis of their own (`ownAxis`).
 * @param {Array} strip  the strip's runs
 * @param {{metrics: Array, values: object}} data  the derived-metrics read
 * @param {string[]} colors  the palette after the eval series
 */
export function runMetricSeries(strip, data, colors = []) {
  return (data?.metrics ?? []).map((m, i) => {
    const percent = m.format === "percent";
    return {
      key: runMetricKey(m),
      name: `${m.name} (run metric)`,
      color: colors[i % Math.max(1, colors.length)],
      data: strip.map((r) => {
        const v = runMetricValue(data?.values, r, m);
        return v == null ? null : percent ? v * 100 : v;
      }),
      runMetric: m,
      ownAxis: !percent,
    };
  });
}

/** The Scores card's axis and tick text when a run metric is picked. */
export function runMetricAxis(series) {
  const m = series?.runMetric;
  if (!m) return null;
  const vals = (series.data ?? []).filter((v) => v != null);
  if (!series.ownAxis)
    return {
      ...zoomRange(vals),
      format: (v) => fmtMetric(v / 100, "percent"),
      note: "run metric · 0–100",
    };
  const lo = vals.length ? Math.min(0, ...vals) : 0;
  return {
    min: lo,
    max: undefined,
    zoomed: false,
    format: (v) => fmtMetric(v, m.format),
    note: `run metric · ${m.higher_is_better === false ? "↓" : "↑"} better`,
  };
}

/**
 * The card's line for a picked run metric: the latest run, the best run (by
 * its own direction) and the baseline, each with its value.
 */
export function runMetricSummary(strip, data, metric, baselineRun) {
  const list = strip
    .map((r) => ({ run: r, value: runMetricValue(data?.values, r, metric) }))
    .filter((x) => x.value != null);
  if (!list.length) return null;
  const better = metric.higher_is_better === false ? -1 : 1;
  const best = list.reduce((a, b) =>
    better * (b.value - a.value) > 0 ? b : a,
  );
  const base = baselineRun
    ? runMetricValue(data?.values, baselineRun, metric)
    : null;
  return {
    latest: list[list.length - 1],
    best,
    vs: base == null ? null : { run: baselineRun, value: base },
    format: (v) => fmtMetric(v, metric.format),
  };
}
