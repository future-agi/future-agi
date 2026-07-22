import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import BudgetRBACSection from "./BudgetRBACSection";

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    isLoading: false,
  }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    isLoading: false,
    data: {
      budgets: {
        per_model: {
          limit: 1000,
          spent: 125,
          alertThreshold: 75,
          onExceed: "block",
        },
      },
      auth: { enabled: true },
      rbac: {},
    },
  }),
  useRemoveBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useSetBudget: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: {
      total_cost: { value: 50 },
      total_requests: { value: 10 },
    },
  }),
}));

describe("BudgetRBACSection", () => {
  it("renders budget dashboard from canonical budget config fields", () => {
    render(<BudgetRBACSection />);

    expect(screen.getByText("Budgets")).toBeInTheDocument();
    expect(screen.getByText("Budget Levels")).toBeInTheDocument();
    expect(screen.getByText("Per Model")).toBeInTheDocument();
    expect(screen.getByText("$125.00 / $1.0K")).toBeInTheDocument();
  });
});
