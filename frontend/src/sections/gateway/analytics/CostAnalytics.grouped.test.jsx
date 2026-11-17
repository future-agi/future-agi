// The Cost tab's range total and the donut's centre use the grids' money
// format (grouped thousands, the same digits), as the KPI does.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";

import CostAnalytics from "./CostAnalytics";

const mockUseAnalyticsCost = vi.fn();
const chartSpy = vi.fn();

vi.mock("./hooks/useAnalyticsCost", () => ({
  useAnalyticsCost: (...args) => mockUseAnalyticsCost(...args),
}));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chartSpy(props);
    return <div data-testid={`chart-${props.type}`} />;
  },
}));

const donut = () =>
  chartSpy.mock.calls.map(([props]) => props).find((p) => p.type === "donut")
    .options.plotOptions.pie.donut.labels;

beforeEach(() => {
  chartSpy.mockReset();
  mockUseAnalyticsCost.mockReturnValue({
    isLoading: false,
    data: {
      total_cost: "26265.65046",
      breakdown: [
        { name: "model-a", total_cost: "18958.5789", request_count: 10 },
        { name: "model-b", total_cost: "7307.07156", request_count: 4 },
      ],
    },
  });
});

describe("Cost tab: totals read like the grids", () => {
  it("groups thousands in the range's total", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    expect(
      screen.getByText(/spent in this range/).closest("p"),
    ).toHaveTextContent(/^\$26,265\.65046 spent in this range$/);
  });

  it("groups thousands in the donut's centre, for the total and a slice", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const labels = donut();
    expect(
      labels.total.formatter({ globals: { seriesTotals: [26265.65046] } }),
    ).toBe("$26,265.65046");
    expect(labels.value.formatter("7307.07156")).toBe("$7,307.07156");
  });
});
