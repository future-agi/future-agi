import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import {
  AGGREGATION_PREPARING_MESSAGE,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import {
  errorPoint,
  invalidCases,
  qualityPoint,
  zeroPoint,
} from "src/utils/__tests__/fixtures/evalQualityCases";
import ChartsWithFetch from "../ChartsWithFetch";
import ChartsGenerator from "../ChartsGenerator";

const state = vi.hoisted(() => ({ query: {}, chart: null, zoom: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => state.query }));
vi.mock("../ChartsTheme", () => ({
  default: (props) => {
    state.chart = props;
    return <div data-testid="metric-chart" />;
  },
}));
vi.mock("../ChartsViewProvider/ChartsViewContext", () => ({
  useChartsViewContext: () => ({
    selectedInterval: "Hour",
    filters: [],
    handleZoomChange: state.zoom,
  }),
}));
vi.mock("src/hooks/use-local-storage", () => ({ getStorage: () => false }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));

const evaluation = { id: "eval-nk20", name: "Quality", output_type: "float" };
const metadata = {
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
};
const series = (name, points) => ({
  name,
  ...metadata,
  data: points.map((point, index) => ({
    ...point,
    timestamp: new Date(Date.UTC(2026, 7, 3, index)).toISOString(),
  })),
});
const setResult = (result, extra = {}) => {
  state.query = {
    data: { data: { result } },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...extra,
  };
};
const chart = () => (
  <ChartsWithFetch evaluation={evaluation} observeId="project-nk20" inView />
);
const tooltip = (
  dataPointIndex,
  seriesIndex = 0,
  collapsedSeriesIndices = [],
) => {
  const current = state.chart;
  const values = current.series.map((s) => s.data.map((point) => point.y));
  const html = current.options.tooltip.custom({
    series: values,
    seriesIndex,
    dataPointIndex,
    w: {
      config: { series: current.series },
      globals: {
        series: values,
        seriesX: current.series.map((s) => s.data.map((point) => point.x)),
        collapsedSeriesIndices,
      },
    },
  });
  const element = document.createElement("div");
  element.innerHTML = html;
  return element.textContent;
};

describe("ChartsWithFetch and ChartsGenerator NK20 copy contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.chart = null;
    state.query = {};
  });

  it("propagates every bucket's metadata into the actual generator callback", () => {
    const points = [
      qualityPoint(),
      errorPoint,
      zeroPoint,
      { ...zeroPoint, value: null },
    ];
    setResult([
      series("selected", points),
      series("other", [zeroPoint, qualityPoint(), errorPoint, zeroPoint]),
    ]);
    render(chart());
    points.forEach((point, index) =>
      expect(state.chart.series[0].data[index]).toMatchObject({
        ...point,
        y: point.value,
      }),
    );
    expect(tooltip(0)).toContain("selected: No data");
    expect(tooltip(0)).toContain("other: 0 %");
    expect(tooltip(1)).toContain("selected: Unscored");
    expect(tooltip(1)).toContain("other: No data");
    expect(tooltip(2, 1)).toContain("selected: 0 %");
    expect(tooltip(2, 1)).toContain("other: Unscored");
    expect(tooltip(3)).toContain("selected: -");
    expect(tooltip(0, 0, [1])).not.toContain("other:");
  });

  it("rejects every invalid count case in the real callback for null and numeric values", () => {
    const points = invalidCases.flatMap(({ point }) => [
      { ...point, value: null },
      { ...point, value: 0 },
    ]);
    setResult([series("selected", points)]);
    render(chart());
    expect(
      points.map((_point, index) =>
        tooltip(index).split("selected: ")[1].trim(),
      ),
    ).toEqual(points.map(() => "-"));
    expect(screen.getByText("Unavailable")).toBeVisible();
  });

  it("summarizes the first series only, never overlapping category totals", () => {
    setResult([series("selected", [errorPoint]), series("other", [zeroPoint])]);
    render(chart());
    expect(screen.getByRole("status").textContent).toBe(
      "Unscored · 1 execution error (100.0%) · 0 quality failures · 0 pending · 0 running · 0 skipped · 1 without a quality score",
    );
  });

  it.each([
    ["empty array", [], "No data"],
    ["empty bucket", [qualityPoint()], "No data"],
    ["absent count metadata", [{ value: null }], "Unavailable"],
    [
      "mixed malformed population",
      [qualityPoint(), invalidCases[8].point],
      "Unavailable",
    ],
  ])("identifies completed EVAL %s explicitly", (_name, points, expected) => {
    setResult([series("selected", points)]);
    render(chart());
    expect(screen.getByRole("status").textContent).toBe(expected);
    expect(screen.getByTestId("metric-chart")).toBeVisible();
  });

  it("reports missing first-series data as Unavailable rather than inventing an empty population", () => {
    setResult([{ name: "selected", ...metadata }]);
    render(chart());
    expect(screen.getByRole("status").textContent).toBe("Unavailable");
    expect(screen.queryByText("No data")).not.toBeInTheDocument();
  });

  it.each(["loading", "pending", "failed", "missing", "malformed"])(
    "never emits an empty EVAL summary for a cold %s read",
    (status) => {
      if (status === "loading") state.query = { isLoading: true };
      else if (status === "pending")
        setResult([
          {
            ...series("selected", []),
            query_complete: false,
            query_status: "pending",
            query_refreshing: true,
          },
        ]);
      else if (status === "failed")
        state.query = { isError: true, refetch: vi.fn() };
      else if (status === "missing") state.query = {};
      else setResult({ unexpected: true });
      render(chart());
      expect(screen.queryByText("No data")).not.toBeInTheDocument();
      expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
      if (status === "failed")
        expect(screen.getByRole("alert")).toHaveTextContent(
          QUERY_FAILED_RETRY_MESSAGE,
        );
      else if (status !== "loading")
        expect(screen.getByRole("status")).toHaveTextContent(
          AGGREGATION_PREPARING_MESSAGE,
        );
      else expect(screen.queryByTestId("metric-chart")).not.toBeInTheDocument();
    },
  );

  it("retains exact values and the refresh failure indicator", () => {
    const result = [series("selected", [errorPoint])];
    setResult(result);
    const view = render(chart());
    expect(tooltip(0)).toContain("selected: Unscored");
    setResult(result, { isError: true });
    view.rerender(chart());
    expect(screen.getByRole("alert")).toHaveTextContent(
      QUERY_FAILED_RETRY_MESSAGE,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Unscored · 1 execution error (100.0%)",
    );
    expect(tooltip(0)).toContain("selected: Unscored");
  });

  it("updates the callback on graph-only refreshes", () => {
    setResult([series("selected", [errorPoint])]);
    const view = render(chart());
    expect(tooltip(0)).toContain("selected: Unscored");
    setResult([series("selected", [qualityPoint()])]);
    view.rerender(chart());
    expect(tooltip(0)).toContain("selected: No data");
    setResult([series("selected", [zeroPoint])]);
    view.rerender(chart());
    expect(tooltip(0)).toContain("selected: 0 %");
  });

  it("preserves non-EVAL null and numeric currency formatting without metadata", () => {
    render(
      <ChartsGenerator
        id="resource"
        label="Cost"
        unit="$"
        series={[
          {
            name: "cost",
            data: [
              { x: Date.UTC(2026, 7, 3), y: null },
              { x: Date.UTC(2026, 7, 3, 1), y: 0 },
            ],
          },
        ]}
      />,
    );
    expect(tooltip(0)).toContain("cost: -");
    expect(tooltip(1)).toContain("cost: $0");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
