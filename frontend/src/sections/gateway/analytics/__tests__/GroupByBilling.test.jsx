import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import CostAnalytics from "../CostAnalytics";

// The analytics API groups
// by billing mode as well as provider, naming the groups "Future AGI
// managed", "Own key" (and "unknown" for requests that recorded none). The
// Usage, Cost and Errors tabs share one Group by list, which offers it.

const mockUseAnalyticsCost = vi.fn();

vi.mock("../hooks/useAnalyticsCost", () => ({
  useAnalyticsCost: (...args) => mockUseAnalyticsCost(...args),
}));

vi.mock("react-apexcharts", () => ({
  default: (props) => <div data-testid={`chart-${props.type}`} />,
}));

describe("Group by billing mode", () => {
  beforeEach(() => {
    mockUseAnalyticsCost.mockReset();
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "3",
        breakdown: [
          { name: "Future AGI managed", total_cost: "2", request_count: 3 },
          { name: "Own key", total_cost: "1", request_count: 4 },
        ],
      },
    });
  });

  it("Cost offers Billing and asks the API for group_by billing_mode", () => {
    render(<CostAnalytics start="2026-10-01" end="2026-10-22" />);

    fireEvent.click(screen.getByRole("button", { name: "Billing mode" }));

    expect(mockUseAnalyticsCost).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "billing_mode" }),
    );
    expect(screen.getByText("Cost by Billing mode")).toBeInTheDocument();
  });
});
