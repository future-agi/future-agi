import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import WidgetChart from "../WidgetChart";
import { TABLE_BUCKET_LIMIT } from "../widgetUtils";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));

vi.mock("react-apexcharts", () => ({ default: () => null }));

const metrics = [
  { id: "cost", name: "Cost", aggregation: "sum" },
  { id: "latency", name: "Latency", aggregation: "avg" },
];
const exactState = {
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
};
const points = (values) =>
  values.map((value, index) => ({
    timestamp: new Date(Date.UTC(2026, 6, 9) + index * 60_000).toISOString(),
    value,
  }));

const setResult = (cost = [25, 50], latency = [12, 0]) => {
  h.query.data = {
    data: {
      result: {
        ...exactState,
        query_completed_at: "2026-08-03T02:00:00Z",
        metrics: metrics.map((metric, index) => ({
          ...metric,
          ...exactState,
          series: [
            { name: "total", data: points(index === 0 ? cost : latency) },
          ],
        })),
      },
    },
  };
};

const savedWidget = (chartType, visibleSeries = ["latency|avg|total"]) => ({
  id: "saved-visible-series",
  query_config: { metrics, granularity: "minute" },
  chart_config: {
    chart_type: chartType,
    visible_series: visibleSeries,
    axis_config: { left_y: { decimals: 2 } },
  },
});

const renderWidget = (chartType, visibleSeries) =>
  render(
    <WidgetChart
      widget={savedWidget(chartType, visibleSeries)}
      globalDateRange={null}
    />,
  );

const bodyRows = () => screen.getAllByRole("row").slice(1);

describe("WidgetChart — saved Metric and Table visibility (W10-DASHBOARDS-L05)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.query.isPending = false;
    h.query.isError = false;
    setResult();
  });

  it("renders only the saved selected metric card", () => {
    renderWidget("metric");

    expect(screen.getByText("Latency (avg)")).toBeInTheDocument();
    expect(screen.queryByText("Cost (sum)")).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading")).toHaveLength(1);
    expect(screen.getByRole("heading")).toHaveTextContent("6.00");
  });

  it("uses only selected series for table headers, cells, and row emphasis", () => {
    renderWidget("table");

    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(
      screen.getByRole("columnheader", { name: "Latency (avg)" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Cost (sum)")).not.toBeInTheDocument();
    const rows = bodyRows();
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getAllByRole("cell")).toHaveLength(2);
    expect(within(rows[0]).getAllByRole("cell")[1]).toHaveTextContent("12.00");
    expect(within(rows[1]).getAllByRole("cell")).toHaveLength(2);
    expect(within(rows[1]).getAllByRole("cell")[1]).toHaveTextContent("0.00");
    // Cost is nonzero here, but a hidden metric must not emphasize this row.
    expect(rows[1]).toHaveStyle({ opacity: "0.5" });
  });

  it("plans capped table buckets from selected series without losing their observations", () => {
    const count = TABLE_BUCKET_LIMIT + 1;
    setResult(
      Array(count).fill(99),
      Array.from({ length: count }, (_, index) =>
        index === 0 ? 12 : index === count - 1 ? 24 : null,
      ),
    );
    renderWidget("table");

    const rows = bodyRows();
    expect(rows).toHaveLength(2);
    expect(screen.getByText(`2 of ${count} buckets`)).toBeInTheDocument();
    expect(within(rows[0]).getAllByRole("cell")[1]).toHaveTextContent("12.00");
    expect(within(rows[1]).getAllByRole("cell")[1]).toHaveTextContent("24.00");
  });

  it("does not create table rows for the tail of a longer hidden series", () => {
    setResult([25, 50], [12]);
    renderWidget("table");

    expect(bodyRows()).toHaveLength(1);
    expect(within(bodyRows()[0]).getAllByRole("cell")[1]).toHaveTextContent(
      "12.00",
    );
  });

  it.each(["metric", "table"])(
    "retains both metrics in %s when the saved selection shows all",
    (chartType) => {
      renderWidget(chartType, null);

      expect(screen.getByText("Latency (avg)")).toBeInTheDocument();
      expect(screen.getByText("Cost (sum)")).toBeInTheDocument();
    },
  );

  it("keeps all pie fallback cards despite an inherited non-pie selection", () => {
    renderWidget("pie");

    expect(screen.getByText("Latency (avg)")).toBeInTheDocument();
    expect(screen.getByText("Cost (sum)")).toBeInTheDocument();
    expect(screen.getAllByRole("heading")).toHaveLength(2);
  });

  it("shows the series with the highest period value by default", () => {
    // One series averages 99 over a single bucket; ten average 50 over two.
    // Summed buckets put each 50 series at 100 and hid the worst one.
    const breakdown = (name, aggregateValue, values) => ({
      name,
      aggregate_value: aggregateValue,
      data: points(values),
    });
    h.query.data = {
      data: {
        result: {
          ...exactState,
          query_completed_at: "2026-08-03T02:00:00Z",
          metrics: [
            {
              id: "latency",
              name: "Latency",
              aggregation: "avg",
              ...exactState,
              series: [
                ...Array.from({ length: 10 }, (_, index) =>
                  breakdown(`service-${index}`, 50, [50, 50]),
                ),
                breakdown("slowest", 99, [99, null]),
              ],
            },
          ],
        },
      },
    };
    render(
      <WidgetChart
        widget={{
          id: "top-series",
          query_config: { metrics: [metrics[1]], granularity: "minute" },
          chart_config: { chart_type: "table" },
        }}
        globalDateRange={null}
      />,
    );

    expect(
      screen.getByRole("columnheader", { name: "slowest" }),
    ).toBeInTheDocument();
    // Time plus the ten visible series.
    expect(screen.getAllByRole("columnheader")).toHaveLength(11);
  });
});
