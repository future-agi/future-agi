import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import BudgetRBACSection from "./BudgetRBACSection";
import SetBudgetDialog from "./SetBudgetDialog";
import { budgetRows, orgBudgetPeriod } from "./budgetLevels";

// Budget, alerting, routing and fallback writes are Owner or Admin only
// (useGatewayAdmin); these cases act as an Admin.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

// R7-BUDGET review fixes:
// - P1: the bars show the request logs' spend; the page says the gateway
//   checks each limit against its own count, which can be lower.
// - P2: a throttle is counted on each gateway replica; every text that states
//   the rate says so.
// - P2: an "organization" or "org" budget set through the API can be removed
//   from the Budgets page again, as it could before.
// - P3: the dialog's limit field names the period the gateway applies (the
//   org's period), not always "Monthly".
// The page is one view on the gateway grid (drawn here as a table by the
// grid stand-in): what the Budget Config tab stated is in the grid's row.
vi.mock(
  "ag-grid-react",
  async () => await import("../providers/__tests__/grid-mock"),
);

let mockConfig;
const mockRemove = vi.fn();

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ isLoading: false, data: mockConfig }),
  useRemoveBudget: () => ({ mutate: mockRemove, isPending: false }),
  useSetBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@tanstack/react-query", async (orig) => ({
  ...(await orig()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("./hooks/useBudgetSpend", () => ({
  useBudgetSpend: () => ({
    data: { monthly: { total_spend: 5 }, daily: { total_spend: 1 } },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: { total_cost: { value: 5 }, total_requests: { value: 3 } },
  }),
}));

function renderPage(path = "/dashboard/gateway/budgets") {
  window.history.pushState({}, "", path);
  return render(
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/gateway/budgets"
          element={<BudgetRBACSection />}
        />
        <Route
          path="/dashboard/gateway/budgets/:tab"
          element={<BudgetRBACSection />}
        />
      </Routes>
    </HelmetProvider>,
  );
}

async function pick(user, label, option) {
  await user.click(screen.getByLabelText(label));
  await user.click(screen.getByRole("option", { name: option }));
}

describe("Budgets (R7-BUDGET review)", () => {
  beforeEach(() => {
    mockOrgLevel = 8;
    mockRemove.mockClear();
    mockConfig = {
      budgets: {
        org_limit: {
          limit: 1,
          on_exceed: "block",
          saved_at: "2026-10-21T08:00:00.000Z",
        },
      },
      org_sections_enforced: ["budgets"],
    };
  });

  it("says the gateway checks each limit against its own count, which can be lower than the bar", () => {
    renderPage();
    expect(screen.getByText("$5.00 / $1.00")).toBeInTheDocument();
    expect(
      screen.getByText(
        /checks each limit against its own count of this spend, which can be lower than the bar after a gateway restart or a change of the budget's period/i,
      ),
    ).toBeInTheDocument();
  });

  it("the Throttle option and its rate say the rate is per gateway replica", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await user.click(screen.getByLabelText(/When Limit Exceeded/));
    expect(
      screen.getByText(
        /allow only the set number of requests a minute on each gateway replica/i,
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /Throttle/ }));
    expect(
      screen.getByText(
        /each gateway replica allows this many requests a minute for this budget, so N replicas allow up to N times this/i,
      ),
    ).toBeInTheDocument();
  });

  it("the budget's row states a throttle per gateway replica", () => {
    mockConfig.budgets.org_limit.on_exceed = "throttle";
    mockConfig.budgets.org_limit.throttle_rpm = 3;
    renderPage();
    expect(
      screen.getByText(
        "3 requests a minute per gateway replica past the limit",
      ),
    ).toBeInTheDocument();
  });

  it("an organization or org budget can be removed from the Budgets page", async () => {
    const user = userEvent.setup();
    mockConfig.budgets = {
      enabled: true,
      organization: { limit: 50, action: "block" },
      org: { limit: 20, action: "warn" },
    };
    expect(
      budgetRows(mockConfig.budgets)
        .filter((r) => r.group === "org")
        .map((r) => [r.level, r.removable]),
    ).toEqual([
      ["organization", true],
      ["org", true],
    ]);
    renderPage();
    const row = screen.getByTestId("budget-row-organization").closest("tr");
    await user.click(
      within(row).getByRole("button", { name: "Remove budget organization" }),
    );
    // It asks first, naming the budget; Cancel removes nothing.
    let dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/^Remove the .+ budget\?/);
    expect(mockRemove).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mockRemove).not.toHaveBeenCalled();
    await user.click(
      within(row).getByRole("button", { name: "Remove budget organization" }),
    );
    dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Remove budget" }),
    );
    expect(mockRemove).toHaveBeenCalledTimes(1);
    expect(mockRemove.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      level: "organization",
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      within(screen.getByTestId("budget-row-org").closest("tr")).getByRole(
        "button",
        { name: "Remove budget org" },
      ),
    ).toBeInTheDocument();
  });

  it("does not remove a confirmed budget after edit access is lost", async () => {
    mockConfig.budgets = {
      enabled: true,
      organization: { limit: 50, action: "block" },
    };
    const user = userEvent.setup();
    const view = render(
      <HelmetProvider>
        <BudgetRBACSection />
      </HelmetProvider>,
    );
    await user.click(
      screen.getByRole("button", { name: "Remove budget organization" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /Remove the .+ budget/,
    );
    mockOrgLevel = 3;
    view.rerender(
      <HelmetProvider>
        <BudgetRBACSection />
      </HelmetProvider>,
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove budget",
      }),
    );
    expect(mockRemove).not.toHaveBeenCalled();
  });

  it("the dialog's limit field names the org's period", async () => {
    const user = userEvent.setup();
    mockConfig.budgets = { org_period: "daily", enabled: true, org_limit: 5 };
    expect(orgBudgetPeriod(mockConfig.budgets)).toBe("daily");
    renderPage();
    await user.click(screen.getByRole("button", { name: /Set budget/i }));
    expect(screen.queryByLabelText(/Monthly Limit/)).not.toBeInTheDocument();
    const field = screen.getByLabelText(/Limit \(\$\)/);
    expect(field).toBeInTheDocument();
    expect(
      screen.getByText(/Maximum spend in USD per day/i),
    ).toBeInTheDocument();
  });

  it("without a period set the limit is per month, and the dialog says so", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await pick(user, /Budget Level/, "Organization");
    expect(
      screen.getByText(/Maximum spend in USD per month/i),
    ).toBeInTheDocument();
    expect(orgBudgetPeriod(undefined)).toBe("monthly");
    expect(orgBudgetPeriod({ default_period: "weekly" })).toBe("weekly");
  });
});
