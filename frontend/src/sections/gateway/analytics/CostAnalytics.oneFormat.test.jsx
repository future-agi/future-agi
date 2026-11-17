// Every cost on the Cost tab's charts reads one way (formatChartCost): the
// value axis, the bars' tooltips, the donut's slices, its tooltip and its
// total. Thousands are grouped on the axis too ("$1,000.00", not "$1000.00"),
// and the donut's total adds the slices' exact amounts, so 0.1 + 0.2 reads
// "$0.30", not a float's trailing digits.
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

const optionsOf = (type) =>
  chartSpy.mock.calls.map(([props]) => props).find((p) => p.type === type)
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

const costData = (breakdown) => ({
  isLoading: false,
  data: {
    total_cost: "0.3",
    breakdown,
  },
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

beforeEach(() => {
  chartSpy.mockReset();
  mockUseAnalyticsCost.mockReturnValue(
    costData([
      { name: "model-a", total_cost: "0.1", request_count: 3 },
      { name: "model-b", total_cost: "0.2", request_count: 2 },
    ]),
  );
});

describe("Cost tab: one cost format on the charts", () => {
  it("groups thousands on the value axis from 1200 px up", () => {
    windowUnder1200(false);
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { xaxis } = optionsOf("bar");
    expect(xaxis.labels.formatter(1000)).toBe("$1,000.00");
    expect(xaxis.labels.formatter(25000)).toBe("$25,000.00");
  });

  it("keeps the narrow axis short from $1,000 up and whole below it", () => {
    windowUnder1200(true);
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { xaxis } = optionsOf("bar");
    expect(xaxis.labels.formatter(25000)).toBe("$25K");
    expect(xaxis.labels.formatter(500)).toBe("$500.00");
  });

  it("reads an amount the same on the axis, the tooltips and the donut", () => {
    windowUnder1200(false);
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const bar = optionsOf("bar");
    const donut = optionsOf("donut");
    const expected = "$1,234.50";
    expect(bar.xaxis.labels.formatter(1234.5)).toBe(expected);
    expect(bar.tooltip.y.formatter(1234.5, { dataPointIndex: 0 })).toBe(
      expected,
    );
    expect(donut.plotOptions.pie.donut.labels.value.formatter(1234.5)).toBe(
      expected,
    );
    expect(donut.tooltip.y.formatter(1234.5)).toBe(expected);
  });

  it("adds the slices' exact amounts in the donut's total", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { total } = optionsOf("donut").plotOptions.pie.donut.labels;
    // ApexCharts hands the formatter the slices as floats.
    expect(total.formatter({ globals: { seriesTotals: [0.1, 0.2] } })).toBe(
      "$0.30",
    );
  });

  it("leaves a slice the legend hid out of the donut's total", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { total } = optionsOf("donut").plotOptions.pie.donut.labels;
    expect(total.formatter({ globals: { seriesTotals: [0, 0.2] } })).toBe(
      "$0.20",
    );
  });

  it("groups thousands in an exact donut total", () => {
    mockUseAnalyticsCost.mockReturnValue(
      costData([
        { name: "model-a", total_cost: "1200.1", request_count: 3 },
        { name: "model-b", total_cost: "800.2", request_count: 2 },
      ]),
    );
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { total } = optionsOf("donut").plotOptions.pie.donut.labels;
    expect(
      total.formatter({ globals: { seriesTotals: [1200.1, 800.2] } }),
    ).toBe("$2,000.30");
  });
});
