import { describe, it, expect } from "vitest";
import { getAggColumnLabel } from "../widgetUtils";
import { ALL_AGGREGATIONS } from "../constants";

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
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("95th Percentile");
  });

  it("returns the real percentile label (25th Percentile)", () => {
    const metrics = [{ aggregation: "p25" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("25th Percentile");
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
