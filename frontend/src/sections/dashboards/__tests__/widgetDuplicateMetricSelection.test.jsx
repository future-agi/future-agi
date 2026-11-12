import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import {
  getDashboardMetricSeriesState,
  makeSeriesKey,
  resolveSavedSelection,
} from "../widgetUtils";

// W10-DASHBOARDS-L06: the same property can be added twice with different
// per-metric filters (Cost/Sum for project A and for project B). Both copies
// used to share every series key, so a saved selection reopened on the wrong
// copy. Selections saved before the fix must keep showing the copy the
// dashboard already showed them, also when the two copies return different
// breakdown buckets (each copy is capped and filtered on its own).
const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
  apex: vi.fn(),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    h.apex(props);
    return null;
  },
}));
vi.mock("../ChartLegend", () => ({ default: () => null }));

const PROJECT_A = "a407da87-7a8c-4e8f-9605-6998b7a69470";
const PROJECT_B = "b407da87-7a8c-4e8f-9605-6998b7a69470";
const LEGACY_KEY = "cost|sum|total";

const costFor = (project) => ({
  id: "cost",
  name: "cost",
  display_name: "Cost",
  type: "system_metric",
  source: "traces",
  aggregation: "sum",
  filters: [
    {
      column_id: "project",
      property_id: "system_attribute:traces:project",
      source: "traces",
      output_type: "string",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: [project],
        col_type: "SYSTEM_METRIC",
      },
    },
  ],
});

// Project A spent $10 and project B $100, so each copy is told apart by value.
const resultFor = (metric, buckets = { total: null }) => {
  const project = metric.filters[0].filter_config.filter_value[0];
  const base = project === PROJECT_A ? 10 : 100;
  return {
    id: metric.id,
    name: metric.display_name,
    aggregation: metric.aggregation,
    unit: "$",
    query_complete: true,
    query_status: "complete",
    query_sampled: false,
    series: Object.entries(buckets).map(([name, offset]) => ({
      name,
      data: [
        { timestamp: "2026-07-09T00:00:00Z", value: base + (offset || 0) },
      ],
    })),
  };
};

const SAVED_METRICS = [costFor(PROJECT_A), costFor(PROJECT_B)];
const RESULT_METRICS = SAVED_METRICS.map((metric) => resultFor(metric));

// What the editor persists: the keys of the indices left checked.
const keysFor = (series, indices) => indices.map((i) => series[i].key);

const previewSeries = () =>
  getDashboardMetricSeriesState(RESULT_METRICS).series;

// Project A's copy returns `bucketsA` and project B's copy `bucketsB`.
const splitResult = (bucketsA, bucketsB) => [
  resultFor(SAVED_METRICS[0], bucketsA),
  resultFor(SAVED_METRICS[1], bucketsB),
];
const splitSeries = (bucketsA, bucketsB) =>
  getDashboardMetricSeriesState(splitResult(bucketsA, bucketsB)).series;

const showSavedLine = (visibleSeries, resultMetrics = RESULT_METRICS) => {
  h.query.data = {
    data: {
      result: {
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_completed_at: "2026-07-09T01:00:00Z",
        metrics: resultMetrics,
      },
    },
  };
  render(
    <WidgetChart
      globalDateRange={null}
      widget={{
        id: "cost-by-project",
        query_config: { metrics: SAVED_METRICS, granularity: "day" },
        chart_config: { chart_type: "line", visible_series: visibleSeries },
      }}
    />,
  );
  return h.apex.mock.calls
    .at(-1)[0]
    .series.map((series) => series.data.map((point) => point.y));
};

beforeEach(() => {
  vi.clearAllMocks();
  h.query.data = null;
});
afterEach(cleanup);

describe("W10-DASHBOARDS-L06 series keys for repeated metrics", () => {
  it("gives each copy of a repeated metric its own key", () => {
    expect(previewSeries().map((series) => series.key)).toEqual([
      "cost|sum#1|total",
      LEGACY_KEY,
    ]);
  });

  it("restores the first copy when only the first copy was kept", () => {
    const series = previewSeries();
    const restored = resolveSavedSelection(keysFor(series, [0]), series);

    expect(restored).toEqual(new Set([0]));
    expect(series[0].data[0].y).toBe(10);
  });

  it("restores both copies when both were kept", () => {
    const series = previewSeries();

    expect(resolveSavedSelection(keysFor(series, [0, 1]), series)).toEqual(
      new Set([0, 1]),
    );
  });

  it("separates every breakdown bucket of the two copies", () => {
    const series = getDashboardMetricSeriesState(
      SAVED_METRICS.map((metric) =>
        resultFor(metric, { "gpt-4o": 0, "gpt-4o-mini": 1 }),
      ),
    ).series;
    const keys = series.map((s) => s.key);

    expect(keys).toEqual([
      "cost|sum#1|gpt-4o",
      "cost|sum#1|gpt-4o-mini",
      "cost|sum|gpt-4o",
      "cost|sum|gpt-4o-mini",
    ]);
    expect(new Set(keys).size).toBe(4);
    expect(resolveSavedSelection([keys[1]], series)).toEqual(new Set([1]));
  });

  it("ranks three copies by how many copies follow them", () => {
    const series = getDashboardMetricSeriesState([
      ...RESULT_METRICS,
      { ...RESULT_METRICS[0] },
    ]).series;

    expect(series.map((s) => s.key)).toEqual([
      "cost|sum#2|total",
      "cost|sum#1|total",
      LEGACY_KEY,
    ]);
  });

  it("keeps a selection saved before the fix on the copy it already showed", () => {
    const series = previewSeries();
    const restored = resolveSavedSelection([LEGACY_KEY], series);

    expect(restored).toEqual(new Set([1]));
    expect(series[1].data[0].y).toBe(100);
    // Reopening and saving again writes the same key back.
    expect(keysFor(series, [...restored])).toEqual([LEGACY_KEY]);
  });

  it("leaves keys of metrics that are not repeated unchanged", () => {
    const series = getDashboardMetricSeriesState([
      RESULT_METRICS[0],
      { ...RESULT_METRICS[1], aggregation: "avg" },
    ]).series;

    expect(series.map((s) => s.key)).toEqual([LEGACY_KEY, "cost|avg|total"]);
  });

  it("keeps the two-argument key format", () => {
    const metric = { id: "m1", aggregation: "avg" };
    expect(makeSeriesKey(metric, "us")).toBe("m1|avg|us");
    expect(makeSeriesKey(metric, "us", 0)).toBe("m1|avg|us");
    expect(makeSeriesKey(metric, "us", 2)).toBe("m1|avg#2|us");
  });
});

describe("W10-DASHBOARDS-L06 saved line widget with a repeated metric", () => {
  it("shows the $10 project the editor kept, not the $100 copy", () => {
    expect(showSavedLine(keysFor(previewSeries(), [0]))).toEqual([[10]]);
  });

  it("shows both projects when both were kept", () => {
    expect(showSavedLine(keysFor(previewSeries(), [0, 1]))).toEqual([
      [10],
      [100],
    ]);
  });

  it("keeps showing project B for a selection saved before the fix", () => {
    expect(showSavedLine([LEGACY_KEY])).toEqual([[100]]);
  });
});

// w10-db-a correctness check: project A's copy returns only gpt-4o and project
// B's only gpt-4o-mini. Before the fix cost|sum|gpt-4o named A's series alone,
// and it still must.
describe("W10-DASHBOARDS-L06 copies that return different breakdown buckets", () => {
  const GPT_4O = { "gpt-4o": 0 };
  const GPT_4O_MINI = { "gpt-4o-mini": 0 };

  it("restores project A's bucket from a selection saved before the fix", () => {
    const series = splitSeries(GPT_4O, GPT_4O_MINI);
    const restored = resolveSavedSelection(["cost|sum|gpt-4o"], series);

    expect(restored).toEqual(new Set([0]));
    expect(series[0].data[0].y).toBe(10);
  });

  it("re-saves it under project A's own key, which keeps naming project A", () => {
    const series = splitSeries(GPT_4O, GPT_4O_MINI);
    const resaved = keysFor(series, [
      ...(resolveSavedSelection(["cost|sum|gpt-4o"], series) ?? []),
    ]);

    expect(resaved).toEqual(["cost|sum#1|gpt-4o"]);
    expect(resolveSavedSelection(resaved, series)).toEqual(new Set([0]));
  });

  it("keeps project A's bucket next to a saved key that still matches", () => {
    const series = splitSeries(GPT_4O, { "gpt-4o-mini": 0, "o3-mini": 1 });

    expect(
      resolveSavedSelection(
        ["cost|sum|gpt-4o", "cost|sum|gpt-4o-mini"],
        series,
      ),
    ).toEqual(new Set([0, 1]));
  });

  it("restores each legacy key to the copy that owned it before the fix", () => {
    // A: gpt-4o (0), gpt-4o-mini (1). B: gpt-4o (2). The base let the last
    // series with a key win, so gpt-4o was B's and gpt-4o-mini A's.
    const series = splitSeries({ "gpt-4o": 0, "gpt-4o-mini": 1 }, GPT_4O);

    expect(resolveSavedSelection(["cost|sum|gpt-4o-mini"], series)).toEqual(
      new Set([1]),
    );
    expect(resolveSavedSelection(["cost|sum|gpt-4o"], series)).toEqual(
      new Set([2]),
    );
  });

  it("keeps naming project A after project B starts returning the same bucket", () => {
    const saved = keysFor(splitSeries(GPT_4O, GPT_4O_MINI), [0]);
    const later = splitSeries(GPT_4O, { "gpt-4o": 0, "gpt-4o-mini": 1 });

    expect(resolveSavedSelection(saved, later)).toEqual(new Set([0]));
    expect(later[0].data[0].y).toBe(10);
  });

  it("shows only project A on the saved line widget", () => {
    expect(
      showSavedLine(["cost|sum|gpt-4o"], splitResult(GPT_4O, GPT_4O_MINI)),
    ).toEqual([[10]]);
  });
});
