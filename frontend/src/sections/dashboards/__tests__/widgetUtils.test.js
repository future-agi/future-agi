import { describe, it, expect } from "vitest";
import {
  fromAxisConfigPayload,
  getAggColumnLabel,
  getYAxisRangeWarning,
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
