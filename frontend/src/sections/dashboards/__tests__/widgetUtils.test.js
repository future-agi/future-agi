// This suite reads the widget sources off disk to prove the saved widget and
// the editor preview resolve their axis through the same helper, so it needs
// `process`. Everything under src/ otherwise lints as browser code.
/* eslint-env node */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import {
  fromAxisConfigPayload,
  getAggColumnLabel,
  getSeriesScalar,
  groupPieSeries,
  isAdditiveAggregation,
  getYAxisRangeWarning,
  getAutoYAxisBounds,
  getFittedYAxisBounds,
  getVisibleIndices,
  resolveAxisBounds,
  resolveWidgetAxisPlan,
  parseBound,
  makeSeriesKey,
  resolveSavedSelection,
  resolveVisibleSeries,
  seriesHasDataPoints,
  toAxisConfigPayload,
} from "../widgetUtils";
import { ALL_AGGREGATIONS } from "../constants";

describe("axis config contract", () => {
  const uiConfig = {
    leftY: { prefixSuffix: "prefix", outOfBounds: "visible", unit: "ms" },
    rightY: { prefixSuffix: "suffix", outOfBounds: "hidden" },
    xAxis: { visible: true },
    seriesAxis: { 0: "right" },
  };

  it("serializes UI state to the snake_case API contract", () => {
    expect(toAxisConfigPayload(uiConfig)).toEqual({
      left_y: {
        prefix_suffix: "prefix",
        out_of_bounds: "visible",
        unit: "ms",
      },
      right_y: { prefix_suffix: "suffix", out_of_bounds: "hidden" },
      x_axis: { visible: true },
      series_axis: { 0: "right" },
    });
  });

  it("restores the snake_case API contract to UI state", () => {
    expect(fromAxisConfigPayload(toAxisConfigPayload(uiConfig))).toEqual(
      uiConfig,
    );
  });

  it("restores legacy camelCase axis configs during rollout", () => {
    expect(fromAxisConfigPayload(uiConfig)).toEqual(uiConfig);
  });
});

describe("seriesHasDataPoints", () => {
  it("returns false when series is empty", () => {
    expect(seriesHasDataPoints([])).toBe(false);
  });

  it("returns false when every series entry has an empty data array", () => {
    expect(
      seriesHasDataPoints([
        { name: "a", data: [] },
        { name: "b", data: [] },
      ]),
    ).toBe(false);
  });

  it("returns true when at least one series entry has data points", () => {
    expect(
      seriesHasDataPoints([
        { name: "a", data: [] },
        { name: "b", data: [{ x: 0, y: 1 }] },
      ]),
    ).toBe(true);
  });

  it("does not crash on a null/undefined series entry", () => {
    // red if the ?. guard on `s` is reverted: series.some((s) => (s.data || [])...) throws
    // TypeError: Cannot read properties of undefined (reading 'data')
    expect(
      seriesHasDataPoints([
        null,
        undefined,
        { name: "a", data: [{ x: 0, y: 1 }] },
      ]),
    ).toBe(true);
    expect(seriesHasDataPoints([null, undefined])).toBe(false);
  });
});

describe("getAggColumnLabel", () => {
  it("returns 'Average' when metrics list is empty", () => {
    expect(getAggColumnLabel([], ALL_AGGREGATIONS)).toBe("Average");
  });

  it("returns 'Average' when a single metric has aggregation 'avg'", () => {
    const metrics = [{ aggregation: "avg" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Average");
  });

  it("returns 'Sum' when a single metric has aggregation 'sum'", () => {
    const metrics = [{ aggregation: "sum" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Sum");
  });

  it("returns 'Median' when all metrics share the median aggregation", () => {
    const metrics = [{ aggregation: "median" }, { aggregation: "median" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Median");
  });

  it("returns the real percentile label (95th Percentile, not 'p95')", () => {
    // red if source drifts from this mock again: WidgetEditorView renders
    // "95th Percentile" for p95, not the raw value "p95".
    const metrics = [{ aggregation: "p95" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe(
      "95th Percentile",
    );
  });

  it("returns the real percentile label (25th Percentile)", () => {
    const metrics = [{ aggregation: "p25" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe(
      "25th Percentile",
    );
  });

  it("returns 'Agg.' when multiple metrics have different aggregations", () => {
    const metrics = [{ aggregation: "sum" }, { aggregation: "count" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Agg.");
  });

  it("coerces undefined aggregation to 'avg', returning 'Average'", () => {
    const metrics = [{ aggregation: undefined }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Average");
  });

  it("falls back to 'Average' when aggregation value is not in allAggregations", () => {
    const metrics = [{ aggregation: "unknown_agg" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("Average");
  });

  it("returns 'Average' when metrics is null or undefined", () => {
    // red if the ?. guard in getAggColumnLabel is reverted to metrics.length
    expect(getAggColumnLabel(null, ALL_AGGREGATIONS)).toBe("Average");
    expect(getAggColumnLabel(undefined, ALL_AGGREGATIONS)).toBe("Average");
  });
});

const series = (values) => [
  { name: "s1", data: values.map((y, i) => ({ x: i, y })) },
];

const leftAxis = (bounds) => ({ leftY: bounds });

describe("getYAxisRangeWarning", () => {
  it("returns null when no min/max is configured", () => {
    expect(getYAxisRangeWarning(series([2, 7]), leftAxis({}))).toBeNull();
    expect(
      getYAxisRangeWarning(series([2, 7]), leftAxis({ min: "", max: "" })),
    ).toBeNull();
  });

  it("warns when every data point falls below the configured min", () => {
    const msg = getYAxisRangeWarning(
      series([2, 7]),
      leftAxis({ min: "34", max: "545" }),
    );
    expect(msg).toBe(
      "Data is outside your configured Y-axis range (34–545). Adjust bounds to see your data.",
    );
  });

  it("warns when every data point falls above the configured max", () => {
    const msg = getYAxisRangeWarning(
      series([900]),
      leftAxis({ min: "34", max: "545" }),
    );
    expect(msg).toBe(
      "Data is outside your configured Y-axis range (34–545). Adjust bounds to see your data.",
    );
  });

  it("returns null when at least one data point is within bounds", () => {
    expect(
      getYAxisRangeWarning(
        series([2, 400]),
        leftAxis({ min: "34", max: "545" }),
      ),
    ).toBeNull();
  });

  it("returns null when there are no numeric data points", () => {
    expect(
      getYAxisRangeWarning(
        series([null, null]),
        leftAxis({ min: "34", max: "545" }),
      ),
    ).toBeNull();
  });

  it("supports a min-only or max-only bound", () => {
    expect(getYAxisRangeWarning(series([2, 7]), leftAxis({ min: "34" }))).toBe(
      "Data is outside your configured Y-axis minimum (34). Adjust bounds to see your data.",
    );
    expect(getYAxisRangeWarning(series([900]), leftAxis({ max: "545" }))).toBe(
      "Data is outside your configured Y-axis maximum (545). Adjust bounds to see your data.",
    );
  });

  it("returns null when a right axis is in use (dual-axis charts unsupported)", () => {
    const axisConfig = {
      leftY: { min: "34", max: "545" },
      rightY: { visible: true },
      seriesAxis: { 0: "right" },
    };
    expect(getYAxisRangeWarning(series([2, 7]), axisConfig)).toBeNull();
  });

  it("treats a non-numeric bound as unset instead of forcing a false-positive warning", () => {
    expect(
      getYAxisRangeWarning(series([2, 7]), leftAxis({ min: "not-a-number" })),
    ).toBeNull();
  });
});

describe("getSeriesScalar", () => {
  const pts = (...ys) => ys.map((y, i) => ({ x: i, y }));

  it("sums buckets for additive aggregations", () => {
    expect(getSeriesScalar(pts(10, 20, 30), "sum")).toBe(60);
    expect(getSeriesScalar(pts(10, 20, 30), "count")).toBe(60);
  });

  it("does not sum count_distinct, whose buckets overlap", () => {
    // The same 100 users active on each of three days is 100 distinct users,
    // not 300. An unweighted mean is the closest answer the per-bucket
    // response supports.
    expect(getSeriesScalar(pts(100, 100, 100), "count_distinct")).toBe(100);
  });

  it("sums the dataset count aggregations too, which are counts like any other", () => {
    // pass_count/fail_count are selectable for dataset metrics; averaging them
    // would report a per-bucket figure as if it were the period total.
    expect(getSeriesScalar(pts(3, 4, 5), "pass_count")).toBe(12);
    expect(getSeriesScalar(pts(3, 4, 5), "fail_count")).toBe(12);
  });

  it("keeps rate aggregations non-additive", () => {
    expect(getSeriesScalar(pts(10, 20), "pass_rate")).toBe(15);
    expect(getSeriesScalar(pts(10, 20), "fail_rate")).toBe(15);
    expect(getSeriesScalar(pts(10, 20), "true_rate")).toBe(15);
  });

  it("takes the maximum bucket for a max aggregation instead of averaging them", () => {
    // Regression for TH-6530: a "max" metric previously showed the MEAN of the
    // per-bucket maxima, e.g. 124.28K instead of the true peak of 396,293.
    expect(getSeriesScalar(pts(2838, 2878, 396293, 95098), "max")).toBe(396293);
  });

  it("takes the minimum bucket for a min aggregation", () => {
    expect(getSeriesScalar(pts(9, 4, 7), "min")).toBe(4);
  });

  it("averages buckets for avg and percentile aggregations", () => {
    expect(getSeriesScalar(pts(10, 20), "avg")).toBe(15);
    expect(getSeriesScalar(pts(10, 20), "p95")).toBe(15);
    expect(getSeriesScalar(pts(10, 20), "median")).toBe(15);
  });

  it("defaults to averaging when the aggregation is unknown or missing", () => {
    expect(getSeriesScalar(pts(10, 20))).toBe(15);
    expect(getSeriesScalar(pts(10, 20), "wat")).toBe(15);
  });

  it("skips null and non-finite buckets rather than counting them as zero", () => {
    expect(getSeriesScalar(pts(10, null, 20), "avg")).toBe(15);
    expect(getSeriesScalar(pts(10, NaN, 20), "sum")).toBe(30);
  });

  it("returns null when there is no usable data", () => {
    expect(getSeriesScalar([], "sum")).toBeNull();
    expect(getSeriesScalar(pts(null, null), "avg")).toBeNull();
  });
});

describe("groupPieSeries", () => {
  const s = (
    metricIndex,
    metricName,
    aggregation,
    unit,
    breakdownName,
    ys,
  ) => ({
    name: `${metricName} / ${breakdownName} (${aggregation})`,
    metricIndex,
    metricName,
    aggregation,
    unit,
    breakdownName,
    data: ys.map((y, i) => ({ x: i, y })),
  });

  it("groups flat series into one entry per metric, valued by that metric's aggregation", () => {
    const groups = groupPieSeries([
      s(0, "Tokens", "avg", "tokens", "proj-a", [10, 20]),
      s(0, "Tokens", "avg", "tokens", "proj-b", [30, 40]),
      s(1, "Latency", "max", "ms", "proj-a", [100, 200]),
      s(1, "Latency", "max", "ms", "proj-b", [300, 400]),
    ]);

    expect(groups).toEqual([
      {
        metricIndex: 0,
        metricName: "Tokens",
        aggregation: "avg",
        unit: "tokens",
        hasValues: true,
        slices: [
          { name: "proj-a", value: 15 },
          { name: "proj-b", value: 35 },
        ],
      },
      {
        metricIndex: 1,
        metricName: "Latency",
        aggregation: "max",
        unit: "ms",
        hasValues: true,
        slices: [
          { name: "proj-a", value: 200 },
          { name: "proj-b", value: 400 },
        ],
      },
    ]);
  });

  it("keeps metrics separate even when they share a name but differ by aggregation", () => {
    const groups = groupPieSeries([
      s(0, "Latency", "avg", "ms", "proj-a", [10, 20]),
      s(1, "Latency", "max", "ms", "proj-a", [10, 20]),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.aggregation)).toEqual(["avg", "max"]);
  });

  it("drops slices with no usable data but keeps the metric", () => {
    const groups = groupPieSeries([
      s(0, "Tokens", "avg", "tokens", "proj-a", [10]),
      s(0, "Tokens", "avg", "tokens", "proj-b", [null]),
      s(1, "Latency", "avg", "ms", "proj-a", [null, null]),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].slices).toEqual([{ name: "proj-a", value: 10 }]);
    expect(groups[1]).toMatchObject({ slices: [], hasValues: false });
  });

  it("drops zero-valued slices, which a ring cannot draw, and the count that implies them", () => {
    // Real case from TH-6530 testing: projects whose traces record no tokens
    // return avg 0, producing invisible slices that still inflated the count.
    const groups = groupPieSeries([
      s(0, "Tokens", "avg", "tokens", "cookbook", [149.89, 0]),
      s(0, "Tokens", "avg", "tokens", "voice-sim", [0, 0]),
      s(0, "Tokens", "avg", "tokens", "local-seed", [0]),
    ]);
    expect(groups[0].slices).toEqual([{ name: "cookbook", value: 74.945 }]);
    expect(groups[0].hasValues).toBe(true);
  });

  it("keeps a metric whose slices are all zero so its panel can explain itself", () => {
    // Dropping the metric outright makes it look like adding it silently
    // failed; the panel stays and reports that every value is zero.
    const groups = groupPieSeries([
      s(0, "Tokens", "avg", "tokens", "a", [0]),
      s(1, "Latency", "avg", "ms", "a", [12]),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      metricName: "Tokens",
      slices: [],
      hasValues: true,
    });
    expect(groups[1].slices).toEqual([{ name: "a", value: 12 }]);
  });

  it("marks a metric with no numeric values at all as having none", () => {
    const groups = groupPieSeries([
      s(0, "Tokens", "avg", "tokens", "a", [null, null]),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ slices: [], hasValues: false });
  });

  it("caps each metric at its own top slices by value, so one metric cannot crowd out another", () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      s(0, "Tokens", "sum", "tokens", `p${i}`, [i + 1]),
    );
    const groups = groupPieSeries([
      ...many,
      s(1, "Latency", "sum", "ms", "only", [5]),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].slices).toHaveLength(10);
    expect(groups[1].slices).toEqual([{ name: "only", value: 5 }]);
  });

  it("folds everything past the cap into one Other slice, so the ring still adds up", () => {
    // 1..12 sums to 78. Dropping the tail would leave the ring normalised over
    // 72 and the centre reporting 72 as the metric's total.
    const groups = groupPieSeries(
      Array.from({ length: 12 }, (_, i) =>
        s(0, "Tokens", "sum", "tokens", `p${i}`, [i + 1]),
      ),
    );
    const [g] = groups;
    expect(g.slices).toHaveLength(10);
    expect(g.slices.slice(0, 9).map((x) => x.value)).toEqual([
      12, 11, 10, 9, 8, 7, 6, 5, 4,
    ]);
    // 3 + 2 + 1, named so the fold is visible rather than silent
    expect(g.slices[9]).toEqual({ name: "Other (3)", value: 6 });
    expect(g.slices.reduce((a, x) => a + x.value, 0)).toBe(78);
  });

  it("does not invent an Other slice for a non-additive aggregation", () => {
    // Summing per-project averages into one "Other" would be a made-up number,
    // so the tail is dropped instead and the centre stays blank.
    const groups = groupPieSeries(
      Array.from({ length: 12 }, (_, i) =>
        s(0, "Latency", "avg", "ms", `p${i}`, [i + 1]),
      ),
    );
    expect(groups[0].slices).toHaveLength(10);
    expect(groups[0].slices.some((x) => /^Other/.test(x.name))).toBe(false);
  });

  it("leaves a metric alone when it is exactly at the cap", () => {
    const groups = groupPieSeries(
      Array.from({ length: 10 }, (_, i) =>
        s(0, "Tokens", "sum", "tokens", `p${i}`, [i + 1]),
      ),
    );
    expect(groups[0].slices).toHaveLength(10);
    expect(groups[0].slices.some((x) => /^Other/.test(x.name))).toBe(false);
  });

  it("returns an empty array for no series", () => {
    expect(groupPieSeries([])).toEqual([]);
  });
});

describe("isAdditiveAggregation", () => {
  it("is true only for aggregations whose slices sum to a real total", () => {
    expect(isAdditiveAggregation("sum")).toBe(true);
    expect(isAdditiveAggregation("count")).toBe(true);
    expect(isAdditiveAggregation("pass_count")).toBe(true);
    expect(isAdditiveAggregation("fail_count")).toBe(true);
  });

  it("is false where summing the slices would invent a quantity", () => {
    // The backend evaluates count_distinct as uniq() per time bucket, so
    // anyone active in more than one bucket is counted once per bucket.
    expect(isAdditiveAggregation("count_distinct")).toBe(false);
    // The sum of three per-project averages is not an average of anything.
    expect(isAdditiveAggregation("avg")).toBe(false);
    expect(isAdditiveAggregation("max")).toBe(false);
    expect(isAdditiveAggregation("min")).toBe(false);
    expect(isAdditiveAggregation("median")).toBe(false);
    expect(isAdditiveAggregation("p95")).toBe(false);
    expect(isAdditiveAggregation("pass_rate")).toBe(false);
    expect(isAdditiveAggregation("true_rate")).toBe(false);
    expect(isAdditiveAggregation()).toBe(false);
  });
});

describe("makeSeriesKey", () => {
  it("builds id|aggregation|bucket", () => {
    expect(makeSeriesKey({ id: "m1", aggregation: "avg" }, "us")).toBe(
      "m1|avg|us",
    );
  });

  it("does not throw on a nullish metric", () => {
    expect(makeSeriesKey(null, "us")).toBe("||us");
    expect(makeSeriesKey(undefined, undefined)).toBe("||");
  });
});

const seriesWithKeys = (keys) => keys.map((key) => ({ key }));

describe("resolveVisibleSeries", () => {
  it("returns null unchanged (all visible)", () => {
    expect(resolveVisibleSeries(null, seriesWithKeys(["a", "b"]))).toBeNull();
  });

  it("maps saved keys to their current indices", () => {
    const result = resolveVisibleSeries(
      ["b", "d"],
      seriesWithKeys(["a", "b", "c", "d"]),
    );
    expect([...result]).toEqual([1, 3]);
  });

  it("drops saved keys whose series no longer exist", () => {
    const result = resolveVisibleSeries(
      ["a", "gone"],
      seriesWithKeys(["a", "b"]),
    );
    expect([...result]).toEqual([0]);
  });

  it("returns an empty Set when a non-empty selection matches nothing", () => {
    const result = resolveVisibleSeries(
      ["old1", "old2"],
      seriesWithKeys(["new1", "new2"]),
    );
    expect(result).toBeInstanceOf(Set);
    expect(result.size).toBe(0);
  });
});

describe("resolveSavedSelection", () => {
  it("returns undefined when nothing was saved (caller applies default)", () => {
    expect(
      resolveSavedSelection(undefined, seriesWithKeys(["a"])),
    ).toBeUndefined();
  });

  it("honors an explicit show-all (null)", () => {
    expect(resolveSavedSelection(null, seriesWithKeys(["a", "b"]))).toBeNull();
  });

  it("honors an intentional hide-all (empty saved list)", () => {
    const result = resolveSavedSelection([], seriesWithKeys(["a", "b"]));
    expect(result).toBeInstanceOf(Set);
    expect(result.size).toBe(0);
  });

  it("honors a saved selection that still matches (including partial)", () => {
    const result = resolveSavedSelection(
      ["b", "gone"],
      seriesWithKeys(["a", "b", "c"]),
    );
    expect([...result]).toEqual([1]);
  });

  it("returns undefined for a fully-stale selection (falls through to default)", () => {
    // Non-empty saved keys, none survive → caller applies its top-10/show-all default.
    expect(
      resolveSavedSelection(["old1", "old2"], seriesWithKeys(["new1", "new2"])),
    ).toBeUndefined();
  });
});

const pts = (...ys) => ys.map((y, i) => ({ x: i, y }));

describe("parseBound", () => {
  it("treats empty, undefined and non-numeric input as unset", () => {
    expect(parseBound("")).toBeNull();
    expect(parseBound(undefined)).toBeNull();
    expect(parseBound("abc")).toBeNull();
    expect(parseBound(NaN)).toBeNull();
  });

  it("returns finite numbers, including zero", () => {
    expect(parseBound(0)).toBe(0);
    expect(parseBound("1500")).toBe(1500);
    expect(parseBound(-4)).toBe(-4);
  });
});

describe("getAutoYAxisBounds", () => {
  const opts = { tickAmount: 5 };

  it("tightens the reported case: peak 7043 gives 7500, not 10000", () => {
    expect(getAutoYAxisBounds([{ data: pts(219, 7043, 1500) }], opts)).toEqual({
      min: 0,
      max: 7500,
    });
  });

  it("never places the max below the peak, and fits exactly when it can", () => {
    expect(getAutoYAxisBounds([{ data: pts(0, 5000) }], opts).max).toBe(5000);
    expect(getAutoYAxisBounds([{ data: pts(0, 200) }], opts).max).toBe(200);
  });

  it("scales across magnitudes", () => {
    expect(getAutoYAxisBounds([{ data: pts(0, 87) }], opts).max).toBe(100);
    expect(getAutoYAxisBounds([{ data: pts(0, 4.2) }], opts).max).toBe(5);
  });

  it("handles sub-1 values without floating point drift", () => {
    const { max } = getAutoYAxisBounds([{ data: pts(0, 0.3) }], opts);
    expect(max).toBeGreaterThanOrEqual(0.3);
    expect(max).toBeLessThanOrEqual(0.5);
  });

  it("sums per bucket for stacked charts", () => {
    const series = [{ data: pts(0, 4000) }, { data: pts(0, 3000) }];
    const stacked = getAutoYAxisBounds(series, { ...opts, stacked: true });
    const plain = getAutoYAxisBounds(series, opts);
    expect(stacked.max).toBeGreaterThanOrEqual(7000);
    expect(plain.max).toBeLessThan(stacked.max);
  });

  it("ignores non-finite values instead of poisoning the peak", () => {
    expect(
      getAutoYAxisBounds([{ data: pts(0, null, NaN, 200) }], opts).max,
    ).toBe(200);
  });

  it("declines a narrow high band, where zero-anchoring would make it worse", () => {
    expect(getAutoYAxisBounds([{ data: pts(40e6, 60e6) }], opts)).toBeNull();
  });

  it("declines cases it cannot scale safely", () => {
    expect(getAutoYAxisBounds([], opts)).toBeNull();
    expect(getAutoYAxisBounds([{ data: [] }], opts)).toBeNull();
    expect(getAutoYAxisBounds([{ data: pts(5) }], opts)).toBeNull();
    expect(getAutoYAxisBounds([{ data: pts(0, 0, 0) }], opts)).toBeNull();
    expect(getAutoYAxisBounds([{ data: pts(-5, 100) }], opts)).toBeNull();
    expect(
      getAutoYAxisBounds([{ data: pts(0, 100) }], {
        ...opts,
        logarithmic: true,
      }),
    ).toBeNull();
  });
});

describe("getVisibleIndices", () => {
  const series = [{ key: "a" }, { key: "b" }, { key: "c" }];

  it("returns every index when nothing is filtered", () => {
    expect(getVisibleIndices(series, null)).toEqual([0, 1, 2]);
  });

  it("returns the original indices of the visible series", () => {
    expect(getVisibleIndices(series, new Set([0, 2]))).toEqual([0, 2]);
  });

  // A top-N selection builds the Set in rank order, so spreading it gives
  // [2, 0] and misaligns with the ascending filter that builds chartSeries.
  it("is ascending even when the Set was built out of order", () => {
    const rankOrdered = new Set([2, 0]);
    expect([...rankOrdered]).toEqual([2, 0]);
    expect(getVisibleIndices(series, rankOrdered)).toEqual([0, 2]);
  });
});

describe("resolveAxisBounds", () => {
  const pts2 = (...ys) => ys.map((y, i) => ({ x: i, y }));
  const series = [{ data: pts2(219, 7043, 1500) }];

  it("auto-scales when nothing is typed", () => {
    expect(resolveAxisBounds(series, {})).toEqual({ min: 0, max: 7500 });
  });

  it("uses a typed bound that does not clip", () => {
    expect(resolveAxisBounds(series, { max: "50000" }).max).toBe(50000);
  });

  it("widens a clipping bound when out of bounds is visible", () => {
    expect(resolveAxisBounds(series, { max: "5000" }).max).toBe(7500);
  });

  it("keeps a clipping bound as a hard cap when hidden", () => {
    expect(
      resolveAxisBounds(series, { max: "5000", outOfBounds: "hidden" }).max,
    ).toBe(5000);
  });

  // The dual-axis case: a small series gets bounds of its own, not the other
  // axis's, so it is not stretched to fill the plot.
  // Dual axis passes fit:true, because a side must always get explicit bounds
  // or ApexCharts scales its series independently.
  it("scales to only the series it is given", () => {
    const small = [{ data: pts2(190, 250, 210) }];
    const { min, max } = resolveAxisBounds(small, {}, { fit: true });
    expect(max).toBeLessThan(1000);
    expect(max).toBeGreaterThanOrEqual(250);
    expect(min).toBeLessThanOrEqual(190);
  });

  // Single-axis takes the same fitted path. Zero-anchoring still wins where the
  // data runs to the floor; this is the case it declines, which used to fall to
  // ApexCharts' coarse ladder and a 190-290 axis for a 190-250 series.
  it("fits a narrow band on a single axis too", () => {
    const small = [{ data: pts2(190, 250, 210) }];
    expect(resolveAxisBounds(small, {}, { fit: true })).toEqual({
      min: 180,
      max: 255,
    });
  });

  it("is unchanged for data that runs to the floor", () => {
    expect(resolveAxisBounds(series, {}, { fit: true })).toEqual({
      min: 0,
      max: 7500,
    });
  });
});

describe("getFittedYAxisBounds", () => {
  const pts = (...ys) => [{ data: ys.map((y, i) => ({ x: i, y })) }];

  // A single example cannot pin this: the axis max is measured from a floor
  // that has already been snapped down onto the step grid, so whether the peak
  // still fits depends on where the span lands on the step ladder. Assert the
  // invariant over a table of spans instead.
  it.each([
    [41, 51],
    [99.9, 100.9],
    [190, 250],
    [179, 479],
    [0.0001, 0.00013],
    [1.001, 1.009],
    [7043, 7100],
    [1000001, 1000009],
    [42, 43],
    [500, 501.5],
  ])("keeps every point inside the axis for [%s, %s]", (floor, peak) => {
    const { min, max } = getFittedYAxisBounds(
      pts(floor, (floor + peak) / 2, peak),
    );
    expect(max).toBeGreaterThanOrEqual(peak);
    expect(min).toBeLessThanOrEqual(floor);
  });

  it("never clips the peak across every integer band up to 200", () => {
    const clipped = [];
    for (let floor = 0; floor <= 200; floor += 1) {
      for (let peak = floor + 1; peak <= 200; peak += 1) {
        const bounds = getFittedYAxisBounds(pts(floor, peak));
        if (!bounds) continue;
        if (bounds.max < peak || bounds.min > floor)
          clipped.push([floor, peak]);
      }
    }
    expect(clipped).toEqual([]);
  });

  it("leaves the round ladder alone where it already fits", () => {
    expect(getFittedYAxisBounds(pts(190, 210, 250))).toEqual({
      min: 180,
      max: 255,
    });
  });

  it("fits a band that dips below zero, where zero-anchoring cannot", () => {
    const { min, max } = getFittedYAxisBounds(pts(-5, 12, 30));
    expect(min).toBeLessThanOrEqual(-5);
    expect(max).toBeGreaterThanOrEqual(30);
  });

  // The carve-outs: no band to fit, so ApexCharts keeps its own scaling.
  it("returns null on a logarithmic side", () => {
    expect(
      getFittedYAxisBounds(pts(41, 45, 51), { logarithmic: true }),
    ).toBeNull();
  });

  it("returns null with fewer than two points", () => {
    expect(getFittedYAxisBounds(pts(42))).toBeNull();
    expect(getFittedYAxisBounds([])).toBeNull();
  });

  it("returns null when every point is the same value", () => {
    expect(getFittedYAxisBounds(pts(7, 7, 7))).toBeNull();
  });
});

describe("resolveWidgetAxisPlan", () => {
  const pts = (...ys) => ys.map((y, i) => ({ x: i, y }));
  const latency = { name: "Latency (avg)", data: pts(219, 7043, 1500) };
  const tokens = { name: "Tokens (avg)", data: pts(41, 45, 51) };
  const dualConfig = {
    leftY: {},
    rightY: { visible: true },
    seriesAxis: { 1: "right" },
  };

  it("gives each side its own bounds when both are drawn", () => {
    const plan = resolveWidgetAxisPlan([latency, tokens], [0, 1], dualConfig);
    expect(plan.hasRightAxis).toBe(true);
    expect(plan.sideOf(0)).toBe("left");
    expect(plan.sideOf(1)).toBe("right");
    expect(plan.bounds.left).toEqual({ min: 0, max: 7500 });
    expect(plan.bounds.right).toEqual({ min: 40, max: 52.5 });
  });

  // The whole point of reading the visible series: with the right-hand series
  // hidden there is no right axis on screen, so the left one must be scaled the
  // way a widget that never had a right axis would scale it.
  it("falls back to single-axis scaling when the right series is hidden", () => {
    const hidden = resolveWidgetAxisPlan([latency], [0], dualConfig);
    const neverHadOne = resolveWidgetAxisPlan([latency], [0], { leftY: {} });

    expect(hidden.hasRightAxis).toBe(false);
    expect(hidden.bounds.right).toBeUndefined();
    expect(hidden.bounds.left).toEqual(neverHadOne.bounds.left);
  });

  it("reads seriesAxis by the original index, not the filtered one", () => {
    // Only the second series is visible, and it is the right-assigned one.
    const plan = resolveWidgetAxisPlan([tokens], [1], dualConfig);
    expect(plan.hasRightAxis).toBe(true);
    expect(plan.sideOf(0)).toBe("right");
  });

  it("stays single-axis when the right axis is switched off", () => {
    const plan = resolveWidgetAxisPlan([latency, tokens], [0, 1], {
      leftY: {},
      rightY: { visible: false },
      seriesAxis: { 1: "right" },
    });
    expect(plan.hasRightAxis).toBe(false);
    expect(plan.sideOf(1)).toBe("left");
  });

  it("survives an empty config", () => {
    const plan = resolveWidgetAxisPlan([latency], [0]);
    expect(plan.hasRightAxis).toBe(false);
    expect(plan.bounds.left).toEqual({ min: 0, max: 7500 });
  });
});

// The saved widget and the editor preview render the same widget through two
// separate files. They used to derive their axis bounds separately, and a fix
// applied to one silently missed the other. Both now go through
// resolveWidgetAxisPlan; this fails the moment either grows its own copy.
describe("the saved widget and the editor preview share one axis plan", () => {
  // Resolve from the vitest root, which is `frontend/` however it was invoked.
  const read = (name) => {
    const rel = join("src", "sections", "dashboards", name);
    const path = [
      join(process.cwd(), rel),
      join(process.cwd(), "frontend", rel),
    ].find(existsSync);
    expect(path, `could not locate ${name}`).toBeDefined();
    return readFileSync(path, "utf8");
  };

  it.each(["WidgetChart.jsx", "WidgetEditorView.jsx"])(
    "%s resolves its y-axis through resolveWidgetAxisPlan, not on its own",
    (file) => {
      const src = read(file);
      expect(src).toContain("resolveWidgetAxisPlan(");
      expect(src).not.toContain("resolveAxisBounds(");
    },
  );
});
