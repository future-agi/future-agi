// In a window under 1200 px (the 1024 layout) the Cost-by-group chart is too
// narrow for six full amounts on its value axis, which ran together. There it
// draws three short ticks ("$25K"); the bars' labels and the tooltips keep
// exact amounts.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

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

const barOptions = () =>
  chartSpy.mock.calls.map(([props]) => props).find((p) => p.type === "bar")
    .options;

const originalMatchMedia = window.matchMedia;
const windowUnder1200 = (under) => {
  window.matchMedia = (query) => ({
    matches: under && query.includes("max-width: 1199.95px"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
};

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

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

describe("Cost by group: the value axis at 1024", () => {
  it("draws at most three short ticks in a window under 1200 px", () => {
    windowUnder1200(true);
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { xaxis } = barOptions();
    expect(xaxis.tickAmount).toBeLessThanOrEqual(3);
    expect(xaxis.labels.formatter(25000)).toBe("$25K");
    expect(xaxis.labels.formatter(12500)).toBe("$12.5K");
  });

  it("keeps the full tick amounts from 1200 px up", () => {
    windowUnder1200(false);
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { xaxis } = barOptions();
    expect(xaxis.tickAmount).toBe(5);
    expect(xaxis.labels.formatter(25000)).not.toBe("$25K");
  });
});
