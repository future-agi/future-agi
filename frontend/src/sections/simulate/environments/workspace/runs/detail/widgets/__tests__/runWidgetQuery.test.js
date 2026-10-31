import { describe, expect, it } from "vitest";
import {
  RUN_RANGE_PRESETS,
  aggregate,
  builtinQuery,
  distinctAttributeValues,
  metricFromCatalog,
  newRunQuery,
  runMetricCatalog,
  runWidgetQuery,
} from "../runWidgetQuery";

// RunTask rows, the shape `mapCallRow` gives.
const task = (id, status, extra = {}) => ({
  id,
  status,
  goal: "Refunds",
  sourceScenario: `scenario-${id}`,
  persona: "Dana",
  trialIndex: 1,
  csat: 7,
  turns: 6,
  latencyMs: 400,
  durationMs: 60000,
  tokens: 1200,
  evalResults: [],
  ...extra,
});

const TASKS = [
  task("a", "passed", {
    persona: "Dana",
    csat: 8,
    evalResults: [{ id: "e1", name: "Policy", score: 1, passed: true }],
  }),
  task("b", "failed", {
    persona: "Lee",
    csat: 3,
    latencyMs: 800,
    evalResults: [{ id: "e1", name: "Policy", score: 0, passed: false }],
  }),
  task("c", "passed", { persona: "Dana", durationMs: 30000 }),
  task("d", "unmeasured", { persona: "Lee", csat: null, latencyMs: null }),
];

const catalog = runMetricCatalog(TASKS);
const metric = (id, aggregation) => ({
  ...metricFromCatalog(catalog.find((item) => item.id === id)),
  ...(aggregation && { aggregation }),
});

describe("runWidgetQuery", () => {
  it("reads only this run; every other range is marked soon", () => {
    expect(
      RUN_RANGE_PRESETS.map((preset) => [preset.value, !!preset.soon]),
    ).toEqual([
      ["this", false],
      ["3", true],
      ["5", true],
      ["10", true],
      ["all", true],
    ]);
    expect(newRunQuery().range).toBe("this");
  });

  it("builds one eval metric per eval the calls were graded with", () => {
    const evalMetric = catalog.find((item) => item.id === "eval:e1");
    expect(evalMetric).toMatchObject({ name: "Policy", category: "evals" });
    expect(catalog.map((item) => item.id)).toEqual(
      expect.arrayContaining([
        "calls",
        "pass_rate",
        "duration",
        "agent_latency",
        "tokens",
        "turns",
        "csat",
      ]),
    );
  });

  it("counts calls and reads the pass rate over measured calls only", () => {
    const { buckets, series } = runWidgetQuery(
      {
        ...newRunQuery(),
        xAxis: "run",
        metrics: [metric("calls"), metric("pass_rate")],
      },
      TASKS,
      { runLabel: "Run 4" },
    );
    expect(buckets.map((bucket) => bucket.label)).toEqual(["Run 4"]);
    expect(series.map((item) => [item.name, item.total])).toEqual([
      ["Calls (count)", 4],
      ["Pass rate (avg)", 66.6667],
    ]);
  });

  it("splits by a breakdown and filters by an attribute", () => {
    const { series } = runWidgetQuery(
      {
        ...newRunQuery(),
        xAxis: "run",
        metrics: [metric("calls")],
        breakdowns: [{ id: "status", name: "Outcome" }],
        filters: [
          { id: "persona", name: "Persona", operator: "is", value: ["Dana"] },
        ],
      },
      TASKS,
    );
    expect(series.map((item) => [item.breakdownName, item.total])).toEqual([
      ["Passed", 2],
    ]);
  });

  it("puts each call on the x-axis in order", () => {
    const { buckets, series } = runWidgetQuery(
      { ...newRunQuery(), metrics: [metric("agent_latency")] },
      TASKS,
    );
    expect(buckets.map((bucket) => bucket.label)).toEqual([
      "C1",
      "C2",
      "C3",
      "C4",
    ]);
    expect(series[0].data.map((point) => point.y)).toEqual([
      400,
      800,
      400,
      null,
    ]);
  });

  it("reads eval pass rates and CSAT buckets", () => {
    const evalSeries = runWidgetQuery(
      { ...newRunQuery(), xAxis: "run", metrics: [metric("eval:e1")] },
      TASKS,
    ).series;
    expect(evalSeries[0].total).toBe(50);

    const csat = runWidgetQuery(
      {
        ...newRunQuery(),
        xAxis: "attr:csat_score",
        metrics: [metric("calls")],
      },
      TASKS,
    );
    expect(csat.buckets).toHaveLength(11);
    expect(csat.series[0].data[3]).toEqual({ x: "3", y: 1 });
    expect(csat.series[0].data[7]).toEqual({ x: "7", y: 1 });
  });

  it("withholds a tail percentile on too few values", () => {
    expect(aggregate([1, 2, 3], "p90")).toBeNull();
    expect(aggregate([1, 2, 3], "p50")).toBe(2);
  });

  it("lists the distinct values of an attribute", () => {
    expect(distinctAttributeValues("persona", TASKS)).toEqual(["Dana", "Lee"]);
  });

  it("maps a built-in to its query where the calls carry its data", () => {
    expect(builtinQuery("goal_outcome", catalog)).toMatchObject({
      chart_type: "pie",
      query: {
        metrics: [expect.objectContaining({ id: "calls" })],
        breakdowns: [{ id: "status", name: "Outcome" }],
      },
    });
    expect(builtinQuery("percentiles", catalog).query.xAxis).toBe("percentile");
    expect(builtinQuery("sentiment", catalog)).toBeNull();
  });
});
