import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import WidgetPieCharts from "../WidgetPieCharts";
import { NO_DATA_FOR_RANGE_MESSAGE } from "../constants";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));

vi.mock("react-apexcharts", () => ({
  default: (props) => (
    <div
      data-testid={`apex-${props.type}`}
      data-series={JSON.stringify(props.series)}
      data-labels={JSON.stringify(props.options?.labels ?? null)}
      data-colors={JSON.stringify(props.options?.colors ?? null)}
      data-yaxis={JSON.stringify(props.options?.yaxis ?? null)}
    />
  ),
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

    expect(items).toEqual(["Latency (p95)", "Latency (p99)", "Latency (p50)"]);
    items.forEach((_, i) => {
      expect(legendColors[i]).toBe(lineColors[i]);
    });
    // The names above hash away from the identity mapping, so a positional
    // legend would disagree here — that is exactly the bug being guarded.
    expect(new Set(legendColors).size).toBe(items.length);
  });
});

// TH-7680: the y-axis left dead space above the data because ApexCharts rounded
// the max onto its own coarse ladder. Auto-scaling fills only the sides the user
// left empty in Threshold Bounds.
describe("WidgetChart — auto-scaled y-axis (TH-7680)", () => {
  const at = (hour, value) => ({
    timestamp: `2026-07-09T${String(hour).padStart(2, "0")}:00:00Z`,
    value,
  });

  const yaxisOf = () =>
    JSON.parse(screen.getByTestId("apex-line").getAttribute("data-yaxis"));

  const renderWith = (axis_config) => {
    h.query.data = queryResult([at(0, 219), at(1, 7043), at(2, 1500)]);
    render(
      <WidgetChart
        widget={{
          ...baseWidget,
          chart_config: {
            ...baseWidget.chart_config,
            ...(axis_config ? { axis_config } : {}),
          },
        }}
        globalDateRange={null}
      />,
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
  });

  it("tightens the max to fit the data when no bounds are set", () => {
    renderWith(null);
    expect(yaxisOf()).toMatchObject({ min: 0, max: 7500 });
  });

  it("lets a typed max win over auto-scaling", () => {
    renderWith({ left_y: { max: "50000" } });
    expect(yaxisOf().max).toBe(50000);
  });

  // 100 sits below the series floor (219), so it clips nothing and survives
  // the Out of Bounds widening — this is the per-side mix, not the widening.
  it("mixes a typed min with an auto max, per side", () => {
    renderWith({ left_y: { min: "100" } });
    expect(yaxisOf()).toMatchObject({ min: 100, max: 7500 });
  });

  it("ignores a non-numeric bound rather than passing NaN to the chart", () => {
    renderWith({ left_y: { max: "abc" } });
    expect(yaxisOf().max).toBe(7500);
  });

  // "Out of Bounds: Visible" has to mean what it says: a typed bound that would
  // cut data off is widened so every point stays on the chart. Hidden keeps the
  // bound as a hard cap and clips. Data peaks at 7043.
  it("widens a typed max that would clip data when Out of Bounds is Visible", () => {
    renderWith({ left_y: { max: "5000", out_of_bounds: "visible" } });
    const { max } = yaxisOf();
    expect(max === undefined || max >= 7043).toBe(true);
  });

  it("clips at the typed max when Out of Bounds is Hidden", () => {
    renderWith({ left_y: { max: "5000", out_of_bounds: "hidden" } });
    expect(yaxisOf().max).toBe(5000);
  });

  it("leaves a typed max alone when no data falls outside it", () => {
    renderWith({ left_y: { max: "50000", out_of_bounds: "visible" } });
    expect(yaxisOf().max).toBe(50000);
  });

  it("widens a typed min that would clip data when Out of Bounds is Visible", () => {
    renderWith({ left_y: { min: "2000", out_of_bounds: "visible" } });
    const { min } = yaxisOf();
    expect(min === undefined || min <= 219).toBe(true);
  });

  // Nothing can be out of bounds when no bounds are typed, so the toggle has
  // nothing to act on and auto-scaling applies either way.
  it("auto-scales regardless of the toggle when no bounds are typed", () => {
    renderWith({ left_y: { out_of_bounds: "hidden" } });
    expect(yaxisOf().max).toBe(7500);
  });

  // A band sitting well above zero cannot be zero-anchored without wasting more
  // space than it saves, and handing it to ApexCharts draws it on a 190-290
  // axis off the coarse {1,2,5,10} ladder. Fit the band where it sits instead.
  it("fits a band that sits well above zero, rather than leaving it to ApexCharts", () => {
    h.query.data = queryResult([at(0, 190), at(1, 250), at(2, 210)]);
    render(<WidgetChart widget={baseWidget} globalDateRange={null} />);
    expect(yaxisOf()).toMatchObject({ min: 180, max: 255 });
  });
});

// TH-7680 follow-up: the dual-axis branch is chosen from the series that are
// actually drawn. Hiding the only right-assigned series from the legend must
// drop back to the single-axis branch, or the left axis quietly switches
// scaling mode — same data, different axis, from a legend click.
describe("WidgetChart — dual axis follows the visible series (TH-7680)", () => {
  const at = (hour, value) => ({
    timestamp: `2026-07-09T${String(hour).padStart(2, "0")}:00:00Z`,
    value,
  });

  const metric = (aggregation, values) => ({
    name: "Latency",
    aggregation,
    series: [{ name: "total", data: values.map((v, i) => at(i, v)) }],
  });

  const dualWidget = (visibleSeries) => ({
    id: "w-1",
    query_config: {
      metrics: [
        { name: "Latency", aggregation: "avg" },
        { name: "Latency", aggregation: "p95" },
      ],
    },
    chart_config: {
      chart_type: "line",
      axis_config: { right_y: { visible: true }, series_axis: { 1: "right" } },
      ...(visibleSeries ? { visible_series: visibleSeries } : {}),
    },
  });

  const yaxisOf = () =>
    JSON.parse(screen.getByTestId("apex-line").getAttribute("data-yaxis"));

  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    h.query.data = null;
    // Left peaks at 7043, right is a narrow 41-51 band.
    h.query.data = {
      data: {
        result: {
          metrics: [
            metric("avg", [219, 7043, 1500]),
            metric("p95", [41, 45, 51]),
          ],
        },
      },
    };
  });

  it("gives each side its own scale while both are visible", () => {
    render(<WidgetChart widget={dualWidget()} globalDateRange={null} />);
    const yaxis = yaxisOf();
    expect(Array.isArray(yaxis)).toBe(true);
    expect(yaxis[0].max).not.toBe(yaxis[1].max);
    // The narrow right band must still fit inside its own axis.
    expect(yaxis[1].max).toBeGreaterThanOrEqual(51);
  });

  it("returns to single-axis scaling when the right series is hidden", () => {
    render(
      <WidgetChart
        widget={dualWidget(["|avg|total"])}
        globalDateRange={null}
      />,
    );
    const yaxis = yaxisOf();
    expect(Array.isArray(yaxis)).toBe(false);
    // Identical to the plain single-axis widget on the same data.
    expect(yaxis).toMatchObject({ min: 0, max: 7500 });
  });
});
