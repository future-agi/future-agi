import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import UsageChart from "../UsageChart";

vi.mock("react-apexcharts", () => ({
  default: ({ options, series }) => (
    <output data-testid="chart">{JSON.stringify({ options, series })}</output>
  ),
}));

const point = { timestamp: "2026-10-24T00:00:00Z", calls: 8 };
const chart = () => JSON.parse(screen.getByTestId("chart").textContent);

describe("Usage chart outcome semantics", () => {
  it("names four passed and four failed completed runs as a 50% Pass rate on series and axis", () => {
    render(<UsageChart data={[{ ...point, pass_count: 4, fail_count: 4 }]} />);
    const { series, options } = chart();
    expect(series[0].data[0].y).toBe(8);
    expect(series[1]).toMatchObject({ name: "Pass rate", data: [{ y: 0.5 }] });
    expect(options.yaxis[1].title.text).toBe("Pass rate");
  });

  it("names score output Average score on series and axis without changing the value", () => {
    render(
      <UsageChart
        data={[{ ...point, avg_score: 0.75 }]}
        outputType="percentage"
      />,
    );
    const { series, options } = chart();
    expect(series[1]).toMatchObject({
      name: "Average score",
      data: [{ y: 0.75 }],
    });
    expect(options.yaxis[1].title.text).toBe("Average score");
  });

  it.each(["pass_fail", "percentage", "deterministic"])(
    "does not manufacture a value series for absent %s outcomes",
    (outputType) => {
      render(<UsageChart data={[point]} outputType={outputType} />);
      expect(chart().series).toEqual([
        {
          name: "Volume",
          type: "bar",
          data: [{ x: Date.parse(point.timestamp), y: 8 }],
        },
      ]);
      expect(chart().options.yaxis).toHaveLength(1);
    },
  );
});
