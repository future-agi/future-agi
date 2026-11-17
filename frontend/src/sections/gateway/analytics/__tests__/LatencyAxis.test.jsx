import React from "react";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import LatencyAnalytics from "../LatencyAnalytics";

const charts = vi.hoisted(() => ({ props: [] }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    charts.props.push(props);
    return <div data-testid="chart" />;
  },
}));
const AXIS_DATA = {
  summary: { p50_ms: 1, p95_ms: 2, p99_ms: 2, avg_ms: 1, total_requests: 3 },
  timeseries: [
    { bucket: "2026-10-21T10:00:00Z", p50_ms: 1, p95_ms: 2, p99_ms: 2 },
  ],
};
const latency = vi.hoisted(() => ({ data: null }));
vi.mock("../hooks/useAnalyticsLatency", () => ({
  useAnalyticsLatency: () => ({ isLoading: false, data: latency.data }),
}));

const LIGHT = createTheme({ palette: palette("light"), typography });

// A fast or quiet gateway's latency chart spans a few milliseconds, so its y
// ticks fall between whole milliseconds. Rounding each tick labelled the axis
// "2ms 2ms 1ms 1ms 0ms"; the axis labels whole milliseconds only, and the
// tooltip still shows each point's own value.
describe("Latency percentiles: the y axis", () => {
  it("labels only whole milliseconds", () => {
    latency.data = AXIS_DATA;
    charts.props = [];
    render(
      <ThemeProvider theme={LIGHT}>
        <LatencyAnalytics
          start="2026-10-20T10:00:00Z"
          end="2026-10-21T10:00:00Z"
          granularity="hour"
        />
      </ThemeProvider>,
    );
    const chart = charts.props.at(-1);
    const label = chart.options.yaxis.labels.formatter;
    expect([0, 0.5, 1, 1.5, 2].map((v) => label(v))).toEqual([
      "0ms",
      "",
      "1ms",
      "",
      "2ms",
    ]);
    expect(label(1500)).toBe("1.50s");
    expect(chart.options.tooltip.y.formatter(1.5)).toBe("2ms");
  });
});

// With no request in the range there is no latency to show: the P50, P95,
// P99 and Average cards read "—", as the KPI strip above them does, never
// "0 ms".
describe("Latency percentiles: the summary cards", () => {
  it("show no latency when there was no request", async () => {
    latency.data = {
      summary: {
        p50_ms: 0,
        p95_ms: 0,
        p99_ms: 0,
        avg_ms: 0,
        total_requests: 0,
      },
      timeseries: [],
    };
    const { getByText, queryByText } = render(
      <ThemeProvider theme={LIGHT}>
        <LatencyAnalytics
          start="2026-10-20T10:00:00Z"
          end="2026-10-21T10:00:00Z"
          granularity="hour"
        />
      </ThemeProvider>,
    );
    for (const label of ["P50", "P95", "P99", "Average"]) {
      expect(getByText(label).nextSibling).toHaveTextContent(/^—$/);
    }
    expect(queryByText("0 ms")).toBeNull();
  });

  it("shows each figure when there were requests", () => {
    latency.data = {
      summary: {
        p50_ms: 120,
        p95_ms: 480,
        p99_ms: 1500,
        avg_ms: 200,
        total_requests: 12,
      },
      timeseries: [],
    };
    const { getByText } = render(
      <ThemeProvider theme={LIGHT}>
        <LatencyAnalytics
          start="2026-10-20T10:00:00Z"
          end="2026-10-21T10:00:00Z"
          granularity="hour"
        />
      </ThemeProvider>,
    );
    expect(getByText("P50").nextSibling).toHaveTextContent("120 ms");
    expect(getByText("P99").nextSibling).toHaveTextContent("1.50 s");
  });
});
