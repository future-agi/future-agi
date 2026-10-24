import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import BudgetRBACSection from "./BudgetRBACSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The gateway admin role check (useGatewayAdmin): setting and removing a budget is
// Owner or Admin only. The cases below run as an Admin unless one says
// otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

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
        hard_limit: {
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
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The bar reads the period's spend from the request logs, never the stored
// "spent" above.
vi.mock("./hooks/useBudgetSpend", () => ({
  useBudgetSpend: () => ({
    data: { monthly: { total_spend: 125, per_user: {}, per_key: {} } },
    isLoading: false,
    isError: false,
  }),
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
    expect(screen.getByText("Hard Limit")).toBeInTheDocument();
    expect(screen.getByText("$125.00 / $1.0K")).toBeInTheDocument();
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the budgets with Set Budget and Remove disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<BudgetRBACSection />);
      expect(screen.getByText("$125.00 / $1.0K")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Set Budget" }));
      expectAdminOnly(screen.getByRole("button", { name: /remove budget/i }));
      mockOrgLevel = 8;
    },
  );

  it.each(ADMIN_LEVELS)("%s can set and remove budgets", (_who, level) => {
    mockOrgLevel = level;
    render(<BudgetRBACSection />);
    expectEditable(screen.getByRole("button", { name: "Set Budget" }));
    expectEditable(screen.getByRole("button", { name: /remove budget/i }));
    mockOrgLevel = 8;
  });
});
