import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import WidgetPieCharts from "../WidgetPieCharts";
import { NO_DATA_FOR_RANGE_MESSAGE } from "../constants";

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
    return (
      <div
        data-testid={`apex-${props.type}`}
        data-series={JSON.stringify(props.series)}
        data-labels={JSON.stringify(props.options?.labels ?? null)}
        data-colors={JSON.stringify(props.options?.colors ?? null)}
      />
    );
  },
}));

vi.mock("../ChartLegend", () => ({
  default: (props) => (
    <div
      data-testid="chart-legend"
      data-items={JSON.stringify(props.items)}
      data-colors={JSON.stringify(props.colors)}
    />
  ),
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

// TH-6530: a pie must never combine unrelated metrics into one donut. With a
// breakdown each metric gets its own pie; with no breakdown there is nothing to
// slice by, so a pie is meaningless and we fall back to the metric-card render.
describe("WidgetChart — pie with multiple metrics (TH-6530)", () => {
  const pt = (value, hour = 0) => ({
    timestamp: `2026-07-09T0${hour}:00:00Z`,
    value,
  });

  const multiMetricResult = (metrics) => ({
    data: { result: { metrics } },
  });

  const pieWidget = (metrics, breakdowns = []) => ({
    id: "w-pie",
    query_config: { metrics, breakdowns },
    chart_config: { chart_type: "pie" },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("renders one donut per metric when three metrics are broken down", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(10), pt(20, 1)] },
          { name: "proj-b", data: [pt(30), pt(40, 1)] },
        ],
      },
      {
        name: "Input Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(5), pt(7, 1)] },
          { name: "proj-b", data: [pt(9), pt(11, 1)] },
        ],
      },
      {
        name: "Latency",
        aggregation: "max",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100), pt(200, 1)] },
          { name: "proj-b", data: [pt(300), pt(400, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Tokens", aggregation: "avg" },
            { name: "Input Tokens", aggregation: "avg" },
            { name: "Latency", aggregation: "max" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(3);

    // Each donut holds ONLY its own metric's slices, valued by that metric's
    // own aggregation: avg for the token metrics, max for latency.
    const seriesOf = (el) => JSON.parse(el.getAttribute("data-series"));
    const labelsOf = (el) => JSON.parse(el.getAttribute("data-labels"));
    expect(donuts.map(seriesOf)).toEqual([
      [15, 35],
      [6, 10],
      [200, 400],
    ]);
    donuts.forEach((d) => expect(labelsOf(d)).toEqual(["proj-a", "proj-b"]));
  });

  it("renders a single donut for one metric broken down, the case that is already correct", () => {
    h.query.data = multiMetricResult([
      {
        name: "Latency",
        aggregation: "avg",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100), pt(200, 1)] },
          { name: "proj-b", data: [pt(300), pt(400, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [{ name: "Latency", aggregation: "avg" }],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(1);
    expect(JSON.parse(donuts[0].getAttribute("data-series"))).toEqual([
      150, 350,
    ]);
  });

  it("falls back to per-metric numbers instead of full circles when there is no breakdown", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "sum",
        unit: "tokens",
        series: [{ name: "total", data: [pt(10), pt(20, 1)] }],
      },
      {
        name: "Latency",
        aggregation: "max",
        unit: "ms",
        series: [{ name: "total", data: [pt(100), pt(200, 1)] }],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget([
          { name: "Tokens", aggregation: "sum" },
          { name: "Latency", aggregation: "max" },
        ])}
        globalDateRange={null}
      />,
    );

    expect(screen.queryByTestId("apex-donut")).not.toBeInTheDocument();
    // sum of buckets for Tokens, max of buckets for Latency — each formatted
    // in its OWN unit, not a blanked shared mixed-unit config.
    expect(screen.getByText("30.00 tokens")).toBeInTheDocument();
    expect(screen.getByText("200.00 ms")).toBeInTheDocument();
  });

  it("keeps every metric's slices when the flat series list exceeds the global top-10 cap", () => {
    // 2 metrics x 6 breakdown values = 12 series. A global top-10 filter would
    // starve the lower-valued metric; the per-metric cap must apply instead.
    const bd = (n) => `p${n}`;
    h.query.data = multiMetricResult([
      {
        name: "Big",
        aggregation: "sum",
        unit: "tokens",
        series: Array.from({ length: 6 }, (_, i) => ({
          name: bd(i),
          data: [pt((i + 1) * 100)],
        })),
      },
      {
        name: "Small",
        aggregation: "sum",
        unit: "tokens",
        series: Array.from({ length: 6 }, (_, i) => ({
          name: bd(i),
          data: [pt(i + 1)],
        })),
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Big", aggregation: "sum" },
            { name: "Small", aggregation: "sum" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    const donuts = screen.getAllByTestId("apex-donut");
    expect(donuts).toHaveLength(2);
    const seriesOf = (el) => JSON.parse(el.getAttribute("data-series"));
    expect(seriesOf(donuts[0])).toHaveLength(6);
    expect(seriesOf(donuts[1])).toHaveLength(6);
    expect(seriesOf(donuts[1])).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("keeps a panel for an all-zero metric instead of silently dropping it", () => {
    // A metric the user added must never just vanish — that reads as the add
    // having failed. Real case: traces that record no time-to-first-token.
    h.query.data = multiMetricResult([
      {
        name: "Latency",
        aggregation: "avg",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(100)] },
          { name: "proj-b", data: [pt(300)] },
        ],
      },
      {
        name: "Time to First Token",
        aggregation: "median",
        unit: "ms",
        series: [
          { name: "proj-a", data: [pt(0)] },
          { name: "proj-b", data: [pt(0)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [
            { name: "Latency", aggregation: "avg" },
            { name: "Time to First Token", aggregation: "median" },
          ],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    // One drawable ring, and the zero metric still has its own labelled panel.
    expect(screen.getAllByTestId("apex-donut")).toHaveLength(1);
    expect(
      screen.getByText("Nothing to chart for this metric"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Time to First Token \(median\)/),
    ).toBeInTheDocument();
  });

  it("shows the no-data message rather than a blank canvas when every bucket is null", () => {
    h.query.data = multiMetricResult([
      {
        name: "Tokens",
        aggregation: "avg",
        unit: "tokens",
        series: [
          { name: "proj-a", data: [pt(null), pt(null, 1)] },
          { name: "proj-b", data: [pt(null), pt(null, 1)] },
        ],
      },
    ]);
    render(
      <WidgetChart
        widget={pieWidget(
          [{ name: "Tokens", aggregation: "avg" }],
          [{ name: "project" }],
        )}
        globalDateRange={null}
      />,
    );

    expect(screen.queryByTestId("apex-donut")).not.toBeInTheDocument();
    expect(screen.getByText(NO_DATA_MESSAGE)).toBeInTheDocument();
  });
});

// Review comment 4 on PR #2074: the guard lived at one of the two call sites,
// so the editor and the saved widget answered all-null data differently.
describe("WidgetPieCharts — nothing to draw in any metric", () => {
  const group = (metricIndex, metricName, hasValues, slices = []) => ({
    metricIndex,
    metricName,
    aggregation: "sum",
    unit: "",
    hasValues,
    slices,
  });
  const renderPies = (groups) =>
    render(
      <WidgetPieCharts
        groups={groups}
        colorFor={() => "#000000"}
        baseFormatConfig={{}}
        fallbackDecimals={2}
      />,
    );

  it("shows a single no-data message, not one panel per metric", () => {
    renderPies([group(0, "Tokens", false), group(1, "Latency", false)]);
    expect(screen.getByText(NO_DATA_FOR_RANGE_MESSAGE)).toBeInTheDocument();
    expect(
      screen.queryAllByText(/Nothing to chart for this metric/),
    ).toHaveLength(0);
  });

  it("still renders per-metric panels when one metric has data", () => {
    renderPies([
      group(0, "Tokens", true, [{ name: "alpha", value: 5 }]),
      group(1, "Latency", false),
    ]);
    expect(screen.queryByText(NO_DATA_FOR_RANGE_MESSAGE)).toBeNull();
    expect(
      screen.getByText(/Nothing to chart for this metric/),
    ).toBeInTheDocument();
  });
});


// Regression guard for TH-7679. The legend used to be handed the raw palette and
// index it positionally (COLORS[i]), while the lines were coloured by a hash of
// the series name — so swatch and line agreed only by coincidence. Both must now
// come from the same per-name lookup.
describe("WidgetChart — legend swatches match the plotted line colours", () => {
  const multiSeriesResult = (aggregations) => ({
    data: {
      result: {
        metrics: aggregations.map((aggregation) => ({
          name: "Latency",
          aggregation,
          series: [
            {
              name: "total",
              data: [
                { timestamp: "2026-07-09T00:00:00Z", value: 12 },
                { timestamp: "2026-07-09T01:00:00Z", value: 18 },
              ],
            },
          ],
        })),
      },
    },
  });

  const multiWidget = (aggregations) => ({
    ...baseWidget,
    query_config: {
      metrics: aggregations.map((aggregation) => ({
        name: "Latency",
        aggregation,
      })),
    },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("gives the legend exactly the colours the chart draws the lines with", () => {
    const aggs = ["p95", "p99", "p50"];
    h.query.data = multiSeriesResult(aggs);
    render(<WidgetChart widget={multiWidget(aggs)} globalDateRange={null} />);

    const legend = screen.getByTestId("chart-legend");
    const chart = screen.getByTestId("apex-line");

    const legendColors = JSON.parse(legend.getAttribute("data-colors"));
    const lineColors = JSON.parse(chart.getAttribute("data-colors"));

    expect(legendColors).toEqual(lineColors);
  });

  it("keeps swatch and line aligned per series name, not per position", () => {
    const aggs = ["p95", "p99", "p50"];
    h.query.data = multiSeriesResult(aggs);
    render(<WidgetChart widget={multiWidget(aggs)} globalDateRange={null} />);

    const legend = screen.getByTestId("chart-legend");
    const items = JSON.parse(legend.getAttribute("data-items"));
    const legendColors = JSON.parse(legend.getAttribute("data-colors"));
    const lineColors = JSON.parse(
      screen.getByTestId("apex-line").getAttribute("data-colors"),
    );

    expect(items).toEqual([
      "Latency (p95)",
      "Latency (p99)",
      "Latency (p50)",
    ]);
    items.forEach((_, i) => {
      expect(legendColors[i]).toBe(lineColors[i]);
    });
    // The names above hash away from the identity mapping, so a positional
    // legend would disagree here — that is exactly the bug being guarded.
    expect(new Set(legendColors).size).toBe(items.length);
  });
});
