/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import UsageChart from "../UsageChart";

vi.mock("react-apexcharts", () => ({
  default: function MockReactApexChart({ options, series, type, height }) {
    return (
      <div
        data-testid="apex-chart"
        data-type={type}
        data-height={height}
        data-series-names={series.map((s) => s.name).join(",")}
        data-yaxis-count={options.yaxis.length}
      >
        {JSON.stringify(series)}
      </div>
    );
  },
}));

const mockDataPoint = (overrides = {}) => ({
  timestamp: "2026-08-01T00:00:00Z",
  calls: 10,
  avg_latency_ms: 120,
  avg_score: 0.8,
  pass_count: 8,
  fail_count: 2,
  ...overrides,
});

describe("UsageChart", () => {
  it("renders a Volume + pass-rate series for pass_fail output", () => {
    render(<UsageChart data={[mockDataPoint()]} outputType="pass_fail" />);

    const chart = screen.getByTestId("apex-chart");
    expect(chart).toHaveAttribute("data-series-names", "Volume,Task Completion Rate");
    expect(chart).toHaveAttribute("data-yaxis-count", "2");
  });

  it("renders a Volume + Task Completion Rate series for percentage output using avg_score", () => {
    render(
      <UsageChart
        data={[mockDataPoint({ avg_score: 0.55 })]}
        outputType="percentage"
      />,
    );

    const chart = screen.getByTestId("apex-chart");
    expect(chart).toHaveAttribute("data-series-names", "Volume,Task Completion Rate");
    expect(chart.textContent).toContain("0.55");
  });

  it("renders only the Volume series for deterministic output (no value axis)", () => {
    render(<UsageChart data={[mockDataPoint()]} outputType="deterministic" />);

    const chart = screen.getByTestId("apex-chart");
    expect(chart).toHaveAttribute("data-series-names", "Volume");
    expect(chart).toHaveAttribute("data-yaxis-count", "1");
  });

  it("renders nothing for an empty data array", () => {
    const { container } = render(<UsageChart data={[]} outputType="pass_fail" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("does not crash on partial/malformed points and treats missing calls as 0", () => {
    const partial = [
      { timestamp: "2026-08-01T00:00:00Z" }, // no calls, no scores at all
      { timestamp: "2026-08-02T00:00:00Z", calls: 5 }, // no pass/fail/score fields
    ];

    render(<UsageChart data={partial} outputType="pass_fail" />);

    const chart = screen.getByTestId("apex-chart");
    // With zero pass+fail on every point, the value series is all-null and
    // gets dropped entirely — only Volume should remain.
    expect(chart).toHaveAttribute("data-series-names", "Volume");
    expect(chart.textContent).toContain('"y":0');
  });

  it("supports the camelCase field aliases (avgScore/passCount/failCount)", () => {
    render(
      <UsageChart
        data={[
          {
            timestamp: "2026-08-01T00:00:00Z",
            calls: 4,
            passCount: 3,
            failCount: 1,
          },
        ]}
        outputType="pass_fail"
      />,
    );

    const chart = screen.getByTestId("apex-chart");
    expect(chart.textContent).toContain('"y":0.75');
  });
});
