import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { render, screen, fireEvent } from "src/utils/test-utils";

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

describe("CostAnalytics", () => {
  beforeEach(() => {
    chartSpy.mockClear();
    mockUseAnalyticsCost.mockReset();
  });

  it("uses a safer donut configuration and filters out zero-cost slices", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "125.5",
        breakdown: [
          { name: "gpt-4o", total_cost: "100" },
          { name: "free-model", total_cost: "0" },
          { name: "gpt-4o-mini", total_cost: "25.5" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    const donutCall = chartSpy.mock.calls
      .map(([props]) => props)
      .find((props) => props.type === "donut");

    expect(donutCall).toBeDefined();
    expect(donutCall.series).toEqual([100, 25.5]);
    expect(donutCall.options.dataLabels.enabled).toBe(false);
    expect(donutCall.options.legend.position).toBe("bottom");
    expect(donutCall.options.plotOptions.pie.customScale).toBe(0.9);
    expect(donutCall.options.plotOptions.pie.donut.labels.name.show).toBe(
      false,
    );
  });

  it("shows empty state when all donut slices are zero", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0",
        breakdown: [
          { name: "gpt-4o", total_cost: "0" },
          { name: "gpt-4o-mini", total_cost: "0" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    expect(screen.getByText("No cost data available.")).toBeInTheDocument();
  });

  it("groups cost by application when that option is picked", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "12",
        breakdown: [
          { name: "checkout", total_cost: "9" },
          { name: "search", total_cost: "3" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Application" }));

    expect(mockUseAnalyticsCost).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "application" }),
    );
    expect(screen.getByText("Cost by Application")).toBeInTheDocument();
  });

  it("keeps the cost axis to a few short ticks so the labels never run together", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0.9",
        breakdown: [
          { name: "checkout", total_cost: "0.8" },
          { name: "search", total_cost: "0.1" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    const barCall = chartSpy.mock.calls
      .map(([props]) => props)
      .find((props) => props.type === "bar");
    const { tickAmount, labels } = barCall.options.xaxis;

    expect(tickAmount).toBe(5);
    expect([0, 0.05, 0.2, 1.5, 0.0005].map(labels.formatter)).toEqual([
      "$0.00",
      "$0.05",
      "$0.20",
      "$1.50",
      "$0.0005",
    ]);
  });
});
