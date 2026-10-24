import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Route, Routes } from "react-router-dom";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import BudgetRBACSection from "./BudgetRBACSection";

// The Budgets page shows each level's real spend for its period, shows and
// edits the budgets switch the gateway reads (one state with Org Config >
// Budgets), says when this gateway does not enforce budgets and states the
// access model.

// The budgets switch is Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

let mockConfig;
let mockSpend;
const mockUpdate = vi.fn();
const mockSpendCalls = [];

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ isLoading: false, data: mockConfig }),
  useRemoveBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useSetBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConfig: () => ({ mutate: mockUpdate, isPending: false }),
}));

vi.mock("@tanstack/react-query", async (orig) => ({
  ...(await orig()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("./hooks/useBudgetSpend", () => ({
  useBudgetSpend: (periods) => {
    mockSpendCalls.push(periods);
    return { data: mockSpend, isLoading: false, isError: false };
  },
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: { total_cost: { value: 50 }, total_requests: { value: 10 } },
  }),
}));

const T1 = "2026-10-21T08:00:00.000Z";
const T2 = "2026-10-21T09:00:00.000Z";

function renderTab(path = "/dashboard/gateway/budgets") {
  window.history.pushState({}, "", path);
  return render(
    <Routes>
      <Route
        path="/dashboard/gateway/budgets"
        element={<BudgetRBACSection />}
      />
      <Route
        path="/dashboard/gateway/budgets/:tab"
        element={<BudgetRBACSection />}
      />
    </Routes>,
  );
}

describe("BudgetRBACSection spend, switch and access", () => {
  beforeEach(() => {
    mockUpdate.mockClear();
    mockSpendCalls.length = 0;
    mockSpend = {
      monthly: { total_spend: 40, per_user: {}, per_key: {} },
      daily: { total_spend: 3, per_user: {}, per_key: {} },
    };
    mockConfig = {
      budgets: {
        org_limit: { limit: 100, on_exceed: "block", saved_at: T1 },
      },
      org_sections_enforced: ["budgets"],
    };
  });

  it("each org-level bar shows the period's real spend", () => {
    renderTab();
    expect(screen.getByText("$40.00 / $100.00")).toBeInTheDocument();
    expect(mockSpendCalls.at(-1)).toEqual(["monthly"]);
  });

  it("a level's own period reads that period's spend", () => {
    mockConfig.budgets = {
      org_limit: 100,
      org_period: "daily",
      enabled: true,
    };
    renderTab();
    expect(screen.getByText("$3.00 / $100.00")).toBeInTheDocument();
    expect(mockSpendCalls.at(-1)).toEqual(["daily"]);
  });

  it("never shows a stored 'spent' nothing writes", () => {
    mockConfig.budgets = {
      org_limit: { limit: 100, spent: 999, on_exceed: "warn", saved_at: T1 },
    };
    renderTab();
    expect(screen.queryByText("$999.00 / $100.00")).not.toBeInTheDocument();
    expect(screen.getByText("$40.00 / $100.00")).toBeInTheDocument();
  });

  it("says which org-level budget is in force when both are set", () => {
    mockConfig.budgets = {
      org_limit: { limit: 100, on_exceed: "warn", saved_at: T1 },
      hard_limit: { limit: 50, on_exceed: "block", saved_at: T2 },
    };
    renderTab();
    const orgRow = screen.getByTestId("budget-row-org_limit");
    expect(
      within(orgRow).getByText(/Not in force: the later Hard Limit save is/i),
    ).toBeInTheDocument();
    const hardRow = screen.getByTestId("budget-row-hard_limit");
    expect(
      within(hardRow).queryByText(/Not in force/i),
    ).not.toBeInTheDocument();
  });

  it("team, user, key and tag budgets are listed per entry, not as one 'teams' bar", () => {
    mockConfig.budgets = {
      enabled: true,
      teams: { eng: { limit: 25, period: "weekly" } },
      users: { alice: { limit: 5 } },
    };
    renderTab();
    expect(screen.queryByText("teams")).not.toBeInTheDocument();
    expect(screen.getByText("Team eng")).toBeInTheDocument();
    expect(screen.getByText("User alice")).toBeInTheDocument();
    expect(
      screen.getAllByText(/spend for this level is counted by the gateway/i)
        .length,
    ).toBeGreaterThan(0);
  });

  it("the budgets switch reads on when a level is set and the org never chose", () => {
    renderTab();
    const sw = screen.getByRole("checkbox", { name: /Enforce budgets/i });
    expect(sw).toBeChecked();
  });

  it("an explicit off reads off and says levels are not enforced", () => {
    mockConfig.budgets = { ...mockConfig.budgets, enabled: false };
    renderTab();
    expect(
      screen.getByRole("checkbox", { name: /Enforce budgets/i }),
    ).not.toBeChecked();
    expect(
      screen.getByText(/Budgets are off for this organization/i),
    ).toBeInTheDocument();
  });

  it("the switch writes budgets.enabled through update-config", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(
      screen.getByRole("checkbox", { name: /Enforce budgets/i }),
    );
    expect(mockUpdate).toHaveBeenCalledTimes(1);
    expect(mockUpdate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { budgets: { enabled: false } },
    });
  });

  it("says when this gateway does not enforce budgets", () => {
    mockConfig.org_sections_enforced = ["cache"];
    renderTab();
    expect(
      screen.getByText(/Not enforced on this gateway/i),
    ).toBeInTheDocument();
  });

  it("says enforcement is unknown when the gateway does not report it", () => {
    delete mockConfig.org_sections_enforced;
    renderTab();
    expect(screen.getByText(/Enforcement unknown/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/Not enforced on this gateway/i),
    ).not.toBeInTheDocument();
  });

  it("claims nothing when the gateway enforces budgets", () => {
    renderTab();
    expect(
      screen.queryByText(/Not enforced on this gateway/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Enforcement unknown/i)).not.toBeInTheDocument();
  });

  it("Roles & Access states the access model when the gateway reports no auth switch", () => {
    renderTab("/dashboard/gateway/budgets/access");
    expect(screen.queryByText("Auth Enabled")).not.toBeInTheDocument();
    expect(screen.queryByText("No")).not.toBeInTheDocument();
    expect(screen.getByText(/by the API key it sends/i)).toBeInTheDocument();
  });

  it("Roles & Access shows the gateway's auth state when it reports one", () => {
    mockConfig.auth = { enabled: true };
    renderTab("/dashboard/gateway/budgets/access");
    expect(screen.getByText("Auth Enabled")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
  });
});
