import { describe, it, expect } from "vitest";
import {
  fromAxisConfigPayload,
  getAggColumnLabel,
  getExactDashboardResult,
  getDashboardMetricSeriesState,
  getPlottedChartSeries,
  getYAxisRangeWarning,
  seriesHasDataPoints,
  shouldConnectAcrossMissingBuckets,
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

describe("getPlottedChartSeries", () => {
  it("connects both line and stacked-line area renderers across missing buckets", () => {
    expect(shouldConnectAcrossMissingBuckets("line")).toBe(true);
    expect(shouldConnectAcrossMissingBuckets("area")).toBe(true);
    expect(shouldConnectAcrossMissingBuckets("bar")).toBe(false);
  });

  it("connects the widget editor line preview across null buckets without changing zeroes or source data", () => {
    const source = [
      {
        name: "Latency (avg)",
        data: [
          { x: 1, y: 12 },
          { x: 2, y: null },
          { x: 3, y: 0 },
          { x: 4, y: 18 },
        ],
      },
    ];

    expect(getPlottedChartSeries(source, true)[0].data).toEqual([
      { x: 1, y: 12 },
      { x: 3, y: 0 },
      { x: 4, y: 18 },
    ]);
    expect(source[0].data).toHaveLength(4);
    expect(source[0].data[1].y).toBeNull();
    expect(getPlottedChartSeries(source, false)).toBe(source);
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

describe("getDashboardMetricSeriesState", () => {
  const point = { timestamp: "2026-07-09T00:00:00Z", value: 12 };
  const sampledMetric = {
    name: "final_status",
    aggregation: "count_distinct",
    query_complete: false,
    query_status: "sampled",
    query_error_code: "sample_limit",
    query_sampling_strategy: "bounded_physical_rows_per_time_bucket",
    query_sampling_interval_seconds: 86400,
    query_sample_limit: 8192,
    query_sample_per_bucket: 128,
    series: [{ name: "total", data: [point] }],
  };
  const completeMetric = {
    name: "latency",
    aggregation: "avg",
    query_complete: true,
    query_status: "complete",
    query_sampled: false,
    series: [{ name: "total", data: [point] }],
  };

  it("fails sampled and degraded metrics closed", () => {
    const degradedMetric = {
      name: "latency",
      aggregation: "avg",
      query_complete: false,
      query_status: "degraded",
      query_error_code: "read_budget_exceeded",
      series: [{ name: "total", data: [point] }],
    };

    const state = getDashboardMetricSeriesState([
      sampledMetric,
      degradedMetric,
    ]);

    expect(state.hasSampledMetrics).toBe(true);
    expect(state.hasDegradedMetrics).toBe(true);
    expect(state.renderableMetrics).toEqual([]);
    expect(state.series).toEqual([]);
  });

  it("fails closed instead of plotting a malformed sample", () => {
    const state = getDashboardMetricSeriesState([
      { ...sampledMetric, query_error_code: "query_failed" },
    ]);

    expect(state.hasSampledMetrics).toBe(false);
    expect(state.hasDegradedMetrics).toBe(true);
    expect(state.series).toEqual([]);
  });

  it("keeps a pending metric non-renderable while an exact snapshot is built", () => {
    const state = getDashboardMetricSeriesState([
      {
        ...completeMetric,
        query_complete: false,
        query_status: "pending",
        query_refreshing: true,
        series: [],
      },
    ]);

    expect(state.hasPendingMetrics).toBe(true);
    expect(state.renderableMetrics).toEqual([]);
    expect(state.series).toEqual([]);
  });

  it.each([
    ["sampled", sampledMetric, true, false],
    [
      "degraded",
      {
        ...sampledMetric,
        query_status: "degraded",
        query_error_code: "read_budget_exceeded",
      },
      false,
      true,
    ],
    [
      "error",
      {
        ...sampledMetric,
        query_complete: undefined,
        query_status: undefined,
        query_error_code: undefined,
        queryReadState: "error",
      },
      false,
      true,
    ],
  ])(
    "fails the whole widget closed for complete + %s metrics",
    (_, unavailableMetric, hasSampled, hasDegraded) => {
      const state = getDashboardMetricSeriesState([
        completeMetric,
        unavailableMetric,
      ]);

      expect(state.hasSampledMetrics).toBe(hasSampled);
      expect(state.hasDegradedMetrics).toBe(hasDegraded);
      expect(state.renderableMetrics).toEqual([]);
      expect(state.series).toEqual([]);
    },
  );
});

describe("getExactDashboardResult", () => {
  it("accepts an all-exact response and rejects one unavailable sibling", () => {
    const exactMetric = {
      name: "latency",
      aggregation: "avg",
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      series: [],
    };
    const exactResult = {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      metrics: [exactMetric],
    };

    expect(getExactDashboardResult({ data: { result: exactResult } })).toBe(
      exactResult,
    );
    expect(
      getExactDashboardResult({
        data: {
          result: {
            query_complete: true,
            query_status: "complete",
            query_sampled: false,
            metrics: [
              exactMetric,
              {
                ...exactMetric,
                query_complete: false,
                query_status: "degraded",
              },
            ],
          },
        },
      }),
    ).toBeNull();
    expect(
      getExactDashboardResult({
        data: { result: { metrics: [exactMetric] } },
      }),
    ).toBeNull();
  });
});

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
