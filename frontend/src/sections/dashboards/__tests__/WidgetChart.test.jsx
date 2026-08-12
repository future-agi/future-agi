import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
  apexProps: null,
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));

vi.mock("react-apexcharts", () => ({
  default: (props) => {
    h.apexProps = props;
    return <div data-testid={`apex-${props.type}`} />;
  },
}));

const baseWidget = {
  id: "w-1",
  query_config: {
    metrics: [{ name: "Latency", aggregation: "avg" }],
  },
  chart_config: { chart_type: "line" },
};

const queryResult = (points) => ({
  data: {
    result: {
      metrics: [
        {
          name: "Latency",
          aggregation: "avg",
          series: [{ name: "total", data: points }],
        },
      ],
    },
  },
});

const NO_DATA_MESSAGE = /No data available for this time period/i;

describe("WidgetChart — empty time-range state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
    h.apexProps = null;
  });

  it("shows the empty-range message when the metric's series has zero data points", () => {
    h.query.data = queryResult([]);
    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-line")).not.toBeInTheDocument();
  });

  it("renders the chart, not the empty-range message, once the series has data points", () => {
    h.query.data = queryResult([
      { timestamp: "2026-07-09T00:00:00Z", value: 12 },
      { timestamp: "2026-07-09T01:00:00Z", value: 18 },
    ]);
    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);

    expect(screen.getByTestId("apex-line")).toBeInTheDocument();
    expect(screen.queryByText(NO_DATA_MESSAGE)).not.toBeInTheDocument();
  });

  // Regression guard: hasNoDataForRange must stay ABOVE the metric-card/table/pie/
  // horizontal early returns so those widget types show this message too, instead of
  // falling into their own type-specific render with an empty series.
  it("shows the empty-range message for a pie widget with zero data points, not the pie render", () => {
    h.query.data = queryResult([]);
    const pieWidget = { ...baseWidget, chart_config: { chart_type: "pie" } };
    render(<WidgetChart widget={pieWidget} globalDateRange={null} />);

    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByTestId("apex-pie")).not.toBeInTheDocument();
  });

  it("renders distribution buckets as a categorical bar chart", () => {
    h.query.data = {
      data: {
        result: {
          metrics: [
            {
              name: "Accuracy",
              aggregation: "count",
              series: [
                {
                  name: "total",
                  data: [
                    { bucket_start: 0, bucket_end: 0.5, value: 3 },
                    { bucket_start: 0.5, bucket_end: 1, value: 7 },
                  ],
                },
              ],
            },
          ],
        },
      },
    };
    const distributionWidget = {
      ...baseWidget,
      query_config: {
        ...baseWidget.query_config,
        query_mode: "distribution",
      },
      chart_config: { chart_type: "distribution" },
    };

    render(<WidgetChart widget={distributionWidget} globalDateRange={null} />);

    expect(screen.getByTestId("apex-bar")).toBeInTheDocument();
    expect(h.apexProps.options.xaxis.type).toBe("category");
    expect(h.apexProps.series[0].data).toEqual([
      { x: "0 - 0.5", y: 3 },
      { x: "0.5 - 1", y: 7 },
    ]);
    expect(h.apexProps.options.tooltip.x.formatter("0 - 0.5")).toBe(
      "Score range: 0 - 0.5",
    );
  });
});
