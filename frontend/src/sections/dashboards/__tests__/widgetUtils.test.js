import { describe, it, expect } from "vitest";
import {
  buildDistributionQueryConfig,
  formatDistributionBucketLabel,
  formatDistributionCount,
  fromAxisConfigPayload,
  getAggColumnLabel,
  getDistributionConfigError,
  getSeriesTotal,
  getYAxisRangeWarning,
  isDistributionMetric,
  getSeriesScalar,
  groupPieSeries,
  isAdditiveAggregation,
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

describe("distribution chart helpers", () => {
  const numericEval = {
    type: "eval_metric",
    source: "traces",
    outputType: "SCORE",
  };

  it("accepts a numeric trace eval as a distribution metric", () => {
    expect(isDistributionMetric(numericEval)).toBe(true);
    expect(isDistributionMetric({ ...numericEval, outputType: "CHOICE" })).toBe(
      false,
    );
    expect(
      isDistributionMetric({ ...numericEval, output_type: "CHOICE" }),
    ).toBe(false);
    expect(
      isDistributionMetric({ ...numericEval, outputType: "NUMERIC" }),
    ).toBe(true);
    expect(
      isDistributionMetric({ ...numericEval, outputType: "Pass/Fail" }),
    ).toBe(false);
    expect(isDistributionMetric({ ...numericEval, outputType: "REASON" })).toBe(
      false,
    );
    expect(isDistributionMetric({ type: "system", source: "traces" })).toBe(
      false,
    );
  });

  it("rejects invalid distribution configurations before the request", () => {
    expect(getDistributionConfigError([numericEval], [])).toBeNull();
    expect(getDistributionConfigError([], [])).toMatch(/exactly one/i);
    expect(
      getDistributionConfigError([numericEval], [{ id: "model" }]),
    ).toMatch(/breakdowns/i);
  });

  it("sends a count query and formats explicit bucket bounds", () => {
    expect(
      buildDistributionQueryConfig({
        metrics: [{ id: "quality", aggregation: "avg" }],
      }),
    ).toEqual({
      query_mode: "distribution",
      metrics: [{ id: "quality", aggregation: "count" }],
    });
    expect(
      formatDistributionBucketLabel({ bucket_start: 0, bucket_end: 0.5 }),
    ).toBe("0 - 0.5");
    expect(
      formatDistributionBucketLabel({ bucket_start: null, bucket_end: 0.5 }),
    ).toBe("Unknown range");
  });

  it("formats distribution counts as whole numbers", () => {
    expect(formatDistributionCount(1200)).toBe("1,200");
    expect(formatDistributionCount("3")).toBe("3");
    expect(formatDistributionCount(undefined)).toBe("-");
  });
});

describe("getSeriesTotal", () => {
  it("sums numeric distribution bucket counts without averaging them", () => {
    expect(getSeriesTotal([{ y: 3 }, { y: 7 }, { y: null }])).toBe(10);
  });

  it("returns null when a series has no numeric values", () => {
    expect(getSeriesTotal([{ y: null }, { y: "not-a-number" }])).toBeNull();
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
