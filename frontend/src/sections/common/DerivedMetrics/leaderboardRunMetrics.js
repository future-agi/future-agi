// HO results R7 (W5 integration B-H2, the leaderboard half): a run metric as a
// leaderboard column. The catalog entry (leaderboardColumns.columnCatalog
// with `runMetrics`, the GET …/derived-metrics/?runs= read) carries the
// metric and its values; this builds the grid's column from it, so every
// cell is the server's value for that run. Pure.
import { fmtMetric, partsLine } from "./formulaModel";
import { runMetricKey, runMetricValue } from "./stripRunMetrics";

export const RUN_METRICS_GROUP = "Run metrics";

/**
 * Catalog entries (one per run metric). A run metric is defined to rank
 * runs, so its column starts shown (the viewer can hide it like any other).
 */
export function runMetricCatalogEntries(runMetrics) {
  return (runMetrics?.metrics ?? []).map((m) => ({
    id: runMetricKey(m),
    label: m.name,
    group: RUN_METRICS_GROUP,
    defaultVisible: true,
    description: `Run metric = ${m.formula}`,
    runMetric: m,
    runMetricValues: runMetrics?.values ?? {},
  }));
}

/**
 * What a run metric reads moving: each run's status, scored tries, finish
 * time and its R1 numbers a formula reads (rows each eval scored, cost,
 * tokens, errors), so the read is asked again when the board's own R1 poll
 * shows a run scoring more rows. A string; equal while nothing moved.
 */
export const runsStamp = (runs) =>
  (runs ?? [])
    .map((r) => {
      const m = r.metrics ?? {};
      const agg = m.aggregates ?? {};
      const scored = Object.keys(agg)
        .sort()
        .map((k) => agg[k]?.n ?? "")
        .join(",");
      return [
        r.run_id,
        r.status ?? "",
        r.success_rate?.total ?? "",
        r.success_rate?.passed ?? "",
        r.completed_at ?? r.updated_at ?? "",
        scored,
        m.cost_usd ?? "",
        m.tokens ?? "",
        m.error_count ?? "",
      ].join(":");
    })
    .join("|");

const nullsLast = (a, b) =>
  a == null && b == null ? 0 : a == null ? 1 : b == null ? -1 : a - b;

/** The AG Grid column for a run-metric catalog entry (`base` = the grid's shared keys). */
export function runMetricColDef(col, base = {}) {
  const m = col.runMetric;
  const values = col.runMetricValues ?? {};
  return {
    ...base,
    width: 140,
    headerTooltip: col.description,
    valueGetter: (p) => (p.data ? runMetricValue(values, p.data, m) : null),
    valueFormatter: (p) =>
      p.value == null ? "—" : fmtMetric(p.value, m.format),
    tooltipValueGetter: (p) => {
      const v = p.data ? values?.[p.data.run_id]?.[m.id] : null;
      return v && v.value != null ? partsLine(m.formula, v, m.format) : null;
    },
    cellStyle: { textAlign: "right", fontVariantNumeric: "tabular-nums" },
    aggFunc: m.higher_is_better === false ? "min" : "max",
    comparator: nullsLast,
  };
}
