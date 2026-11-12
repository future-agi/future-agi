// HO results R7 (W5 B-H2, the leaderboard half): a run metric is a shown
// leaderboard column whose every cell is the derived-metrics read's value
// for that run (never computed here), formatted in the metric's format.
import { describe, expect, it } from "vitest";
import {
  RUN_METRICS_GROUP,
  runMetricCatalogEntries,
  runMetricColDef,
} from "../leaderboardRunMetrics";
import { runMetricKey } from "../stripRunMetrics";
import { fmtMetric } from "../formulaModel";
import {
  COLUMN_GROUPS,
  columnCatalog,
} from "src/sections/develop-detail/ExperimentTab/leaderboardColumns";

const ship = {
  id: "m1",
  name: "ship_score",
  formula: "0.7 * correctness - 10 * cost_usd",
  format: "number",
  higher_is_better: true,
};
const cost = {
  id: "m2",
  name: "cost_share",
  formula: "cost_usd / 2",
  format: "usd",
  higher_is_better: false,
};
const runMetrics = {
  metrics: [ship, cost],
  values: {
    r1: {
      m1: { value: 0.61, parts: { correctness: 0.9, cost_usd: 0.002 } },
      m2: { value: 0.001, parts: { cost_usd: 0.002 } },
    },
    r2: { m1: { value: null, parts: {} } },
  },
};

describe("run metrics as leaderboard columns", () => {
  it("one shown catalog entry per run metric, in its own group", () => {
    const entries = runMetricCatalogEntries(runMetrics);
    expect(entries.map((e) => e.id)).toEqual([
      runMetricKey(ship),
      runMetricKey(cost),
    ]);
    expect(entries.every((e) => e.group === RUN_METRICS_GROUP)).toBe(true);
    expect(entries.every((e) => e.defaultVisible)).toBe(true);
    expect(entries[0].description).toBe(`Run metric = ${ship.formula}`);
    expect(runMetricCatalogEntries(null)).toEqual([]);
    expect(COLUMN_GROUPS).toContain(RUN_METRICS_GROUP);
    const catalog = columnCatalog([], [], { runMetrics });
    expect(
      catalog.filter((c) => c.group === RUN_METRICS_GROUP).map((c) => c.id),
    ).toEqual(entries.map((e) => e.id));
    expect(
      columnCatalog([], []).filter((c) => c.group === RUN_METRICS_GROUP),
    ).toEqual([]);
  });

  it("cells read the server's value per run and keep its format", () => {
    const [shipCol, costCol] = runMetricCatalogEntries(runMetrics);
    const def = runMetricColDef(shipCol, { sortable: true });
    expect(def.sortable).toBe(true);
    expect(def.valueGetter({ data: { run_id: "r1" } })).toBe(0.61);
    expect(def.valueGetter({ data: { run_id: "r2" } })).toBeNull();
    expect(def.valueGetter({ data: { run_id: "r3" } })).toBeNull();
    expect(def.valueFormatter({ value: 0.61 })).toBe(fmtMetric(0.61, "number"));
    expect(def.valueFormatter({ value: null })).toBe("—");
    expect(def.tooltipValueGetter({ data: { run_id: "r1" } })).toBeTruthy();
    expect(def.tooltipValueGetter({ data: { run_id: "r2" } })).toBeNull();
    // A group row shows the best run: max when higher is better, else min.
    expect(def.aggFunc).toBe("max");
    const costDef = runMetricColDef(costCol);
    expect(costDef.aggFunc).toBe("min");
    expect(costDef.valueFormatter({ value: 0.001 })).toBe(
      fmtMetric(0.001, "usd"),
    );
    // Runs without a value sort last either way.
    expect([3, null, 1].sort(def.comparator)).toEqual([1, 3, null]);
  });
});
