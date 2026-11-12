import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import LatencyAnalytics from "./LatencyAnalytics";

const state = vi.hoisted(() => ({ data: null, series: null }));
vi.mock("./hooks/useAnalyticsLatency", () => ({
  useAnalyticsLatency: () => ({ data: state.data }),
}));
vi.mock("react-apexcharts", () => ({
  default: ({ series }) => {
    state.series = series;
    return <div data-testid="latency-chart" />;
  },
}));
const point = (requestCount, minute, value = 0) => ({
  bucket: `2026-11-09T12:${minute}:00Z`,
  request_count: requestCount,
  p50_ms: value,
  p95_ms: value,
  p99_ms: value,
});

describe("latency observation coverage", () => {
  it("shows no requests, not a zero-latency line, for empty filled buckets", () => {
    state.data = {
      summary: { total_requests: 0 },
      timeseries: [point(0, "00"), point(0, "05")],
    };
    render(<LatencyAnalytics granularity="minute" />);
    expect(screen.getByText("No requests in this range.")).toBeInTheDocument();
    expect(screen.queryByTestId("latency-chart")).not.toBeInTheDocument();
  });

  it("renders idle buckets as gaps but preserves a measured zero", () => {
    state.data = {
      summary: { total_requests: 2 },
      timeseries: [point(1, "00", 10), point(0, "05"), point(1, "10", 0)],
    };
    render(<LatencyAnalytics granularity="minute" />);
    expect(screen.getByTestId("latency-chart")).toBeInTheDocument();
    for (const series of state.series) {
      expect(series.data.map(({ y }) => y)).toEqual([10, null, 0]);
    }
  });
});
