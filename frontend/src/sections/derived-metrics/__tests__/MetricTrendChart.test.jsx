import { afterEach, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import MetricTrendChart from "../MetricTrendChart";
import { metricTrendPoints } from "../metricPageValues";
import { formatMetricWindow } from "../metricTime";
import { trend } from "./metricPageFixtures";
const mock = vi.hoisted(() => ({ props: null }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    mock.props = props;
    return <div data-testid="chart" />;
  },
}));
afterEach(cleanup);
it("preserves exact zero and null gaps with a straight single-series line", () => {
  render(<MetricTrendChart data={trend} name="Resolution rate" />);
  expect(metricTrendPoints(trend.results).map((p) => p.y)).toEqual([
    0.62,
    null,
    0,
  ]);
  expect(mock.props.series).toHaveLength(1);
  expect(mock.props.options.stroke).toEqual({ width: 2, curve: "straight" });
  expect(mock.props.options.xaxis.labels.datetimeUTC).toBe(true);
  expect(mock.props.options.xaxis.min).toBe(
    Date.parse(trend.effective_window.window_start),
  );
  expect(mock.props.options.xaxis.max).toBe(
    Date.parse(trend.effective_window.window_end),
  );
  expect(mock.props.options.markers.showNullDataPoints).toBe(false);
  expect(mock.props.options.dataLabels.enabled).toBe(false);
  expect(mock.props.options.tooltip.intersect).toBe(false);
});
it("provides a keyboard-accessible table with coverage, intervals and no-value reasons", () => {
  render(<MetricTrendChart data={trend} name="Resolution rate" />);
  fireEvent.click(screen.getByRole("button", { name: "View data table" }));
  const table = screen.getByRole("table", { name: "Metric trend data" });
  expect(table).toBeVisible();
  expect(within(table).getByText("No value: missing inputs")).toBeVisible();
  expect(within(table).getByText("0%")).toBeVisible();
  expect(within(table).getAllByText("95% interval: 51% – 70%")).toHaveLength(2);
  expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
});
it("states the effective window once, unless the caller already shows it", () => {
  const line = `${formatMetricWindow(trend.effective_window)} · ${trend.effective_window.rounding}`;
  const { rerender } = render(
    <MetricTrendChart data={trend} name="Resolution rate" />,
  );
  expect(screen.getByText(line)).toBeVisible();
  rerender(
    <MetricTrendChart data={trend} name="Resolution rate" showWindow={false} />,
  );
  expect(screen.queryByText(line)).not.toBeInTheDocument();
});
it("shows no-values state without plotting a fabricated line", () => {
  render(<MetricTrendChart data={{ ...trend, results: [] }} name="Cost" />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "No values in this window",
  );
  expect(screen.queryByTestId("chart")).not.toBeInTheDocument();
});
it("inserts untrusted names and units as tooltip text, never HTML", () => {
  const data = {
    ...trend,
    results: trend.results.map((p) => ({
      ...p,
      rate_eligible: false,
      unit: "<img src=x onerror=alert(1)>",
    })),
  };
  render(<MetricTrendChart data={data} name="<b>Unsafe name</b>" />);
  const node = mock.props.options.tooltip.custom({ dataPointIndex: 0 });
  expect(node.querySelector("img")).toBeNull();
  expect(node.querySelector("b")).toBeNull();
  expect(node.textContent).toContain("<b>Unsafe name</b>");
  expect(node.textContent).toContain("<img src=x onerror=alert(1)>");
});
it.each(["light", "dark"])(
  "uses host-theme ink and surfaces in %s mode",
  (mode) => {
    const theme = createTheme({ palette: palette(mode) });
    render(<MetricTrendChart data={trend} name="Resolution rate" />, { theme });
    expect(mock.props.options.theme.mode).toBe(mode);
    expect(mock.props.options.colors).toEqual([theme.palette.text.primary]);
    expect(mock.props.options.markers.strokeColors).toBe(
      theme.palette.background.paper,
    );
    expect(mock.props.options.grid.borderColor).toBe(theme.palette.divider);
  },
);
