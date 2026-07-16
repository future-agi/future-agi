import { describe, it, expect } from "vitest";
import {
  getAggColumnLabel,
  getYAxisRangeWarning,
  seriesHasDataPoints,
} from "../widgetUtils";
import { ALL_AGGREGATIONS } from "../constants";

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
