import { describe, it, expect } from "vitest";
import { getAggColumnLabel } from "../widgetUtils";

const ALL_AGGREGATIONS = [
  { label: "Sum", value: "sum" },
  { label: "Average", value: "avg" },
  { label: "Median", value: "median" },
  { label: "Distinct Count", value: "count_distinct" },
  { label: "Count", value: "count" },
  { label: "Minimum", value: "min" },
  { label: "Maximum", value: "max" },
  { label: "p50", value: "p50" },
  { label: "p75", value: "p75" },
  { label: "p90", value: "p90" },
  { label: "p95", value: "p95" },
  { label: "p99", value: "p99" },
];

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

  it("returns correct percentile label (p95)", () => {
    const metrics = [{ aggregation: "p95" }];
    expect(getAggColumnLabel(metrics, ALL_AGGREGATIONS)).toBe("p95");
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
});
