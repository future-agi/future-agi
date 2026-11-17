import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import {
  getDashboardMetricSeriesState,
  getSuggestedUnitConfig,
  groupPieSeries,
} from "../widgetUtils";

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
    return <div data-testid="count-unit-chart" />;
  },
}));
vi.mock("../ChartLegend", () => ({ default: () => null }));

const COUNT_AGGREGATIONS = [
  "count",
  "count_distinct",
  "pass_count",
  "fail_count",
];
const METRICS = [
  { id: "cost", name: "Cost", unit: "$" },
  { id: "latency", name: "Latency", unit: "ms" },
  { id: "tokens", name: "Tokens", unit: "tokens" },
];
const COUNT_CASES = COUNT_AGGREGATIONS.flatMap((aggregation) =>
  METRICS.map((metric) => ({ ...metric, aggregation })),
);

const metricResult = (metric, value = 100, breakdownName = "total") => ({
  ...metric,
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
  series: [
    {
      name: breakdownName,
      data: [{ timestamp: "2026-07-09T00:00:00Z", value }],
    },
  ],
});

const showWidget = (
  metrics,
  chartType,
  { axisUnit = "", breakdowns = [] } = {},
) => {
  h.query.data = {
    data: {
      result: {
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_completed_at: "2026-07-09T01:00:00Z",
        metrics,
      },
    },
  };
  return render(
    <WidgetChart
      globalDateRange={null}
      widget={{
        id: "count-units",
        query_config: { metrics, breakdowns },
        chart_config: {
          chart_type: chartType,
          axis_config: {
            left_y: { unit: axisUnit, prefix_suffix: "prefix" },
          },
        },
      }}
    />,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  h.query.data = null;
});
afterEach(cleanup);

describe("W10-DASHBOARDS-L02 count units", () => {
  it.each(COUNT_CASES)(
    "normalizes $id/$aggregation before saved and preview series are constructed",
    (metric) => {
      const response = metricResult(metric);
      const { series } = getDashboardMetricSeriesState([response]);
      expect(getSuggestedUnitConfig([metric]).unit).toBe("");
      expect(series).toHaveLength(1);
      expect(series[0].unit).toBe("");
      expect(groupPieSeries(series)[0].unit).toBe("");
      expect(response.unit).toBe(metric.unit);
    },
  );

  describe.each(["metric", "table", "pie"])("%s view", (chartType) => {
    it.each(COUNT_CASES)(
      "renders $id/$aggregation as a unitless count",
      (metric) => {
        showWidget([metricResult(metric)], chartType);
        expect(screen.getByText("100.00")).toBeInTheDocument();
      },
    );

    it("does not restore currency from a previously saved axis unit", () => {
      showWidget([metricResult(COUNT_CASES[0])], chartType, { axisUnit: "$" });
      expect(screen.getByText("100.00")).toBeInTheDocument();
      expect(screen.queryByText("$100.00")).not.toBeInTheDocument();
    });

    it("keeps the count of 100 cost records distinct from their $1 total", () => {
      showWidget(
        [
          metricResult(COUNT_CASES[0]),
          metricResult({ ...METRICS[0], aggregation: "sum" }, 1),
        ],
        chartType,
        { axisUnit: "$" },
      );
      expect(screen.getByText("100.00")).toBeInTheDocument();
      expect(screen.getByText("$1.00")).toBeInTheDocument();
    });
  });

  it.each(COUNT_AGGREGATIONS)(
    "keeps a broken-down cost/%s pie tooltip unitless",
    (aggregation) => {
      showWidget(
        [metricResult({ ...METRICS[0], aggregation }, 100, "project-a")],
        "pie",
        { axisUnit: "$", breakdowns: [{ name: "project" }] },
      );
      const props = h.apex.mock.calls.at(-1)[0];
      expect(props.type).toBe("donut");
      expect(props.options.tooltip.y.formatter(100)).toBe("100.00");
    },
  );

  it("labels a counted table column without the saved currency unit", () => {
    showWidget(
      [
        metricResult(COUNT_CASES[0]),
        metricResult({ ...METRICS[0], aggregation: "sum" }, 1),
      ],
      "table",
      { axisUnit: "$" },
    );
    expect(
      screen.getByRole("columnheader", { name: "Cost (count)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Cost (sum) ($)" }),
    ).toBeInTheDocument();
  });

  it.each([
    { ...METRICS[0], aggregation: "sum", value: 1, expected: "$1.00" },
    { ...METRICS[1], aggregation: "avg", value: 25, expected: "25.00 ms" },
    { ...METRICS[2], aggregation: "max", value: 25, expected: "25.00 tokens" },
  ])("preserves $id/$aggregation units", ({ value, expected, ...metric }) => {
    const response = metricResult(metric, value);
    const { series } = getDashboardMetricSeriesState([response]);
    expect(series[0].unit).toBe(metric.unit);
    showWidget([response], "metric");
    expect(screen.getByText(expected)).toBeInTheDocument();
  });
});
