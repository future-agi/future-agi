import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Link, Route, Routes } from "react-router-dom";
import OrgConfigSection from "../settings/OrgConfigSection";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import BudgetRBACSection from "./BudgetRBACSection";

// R7-BUDGET: the Budgets page shows each level's real spend for its period
// (NG-32), shows and edits the budgets switch the gateway reads (NG-02, one
// state with Org Config > Budgets) and says when this gateway does not
// enforce budgets. The page is one view on the gateway grid
// (drawn here as a table by the grid stand-in); the Roles & Access tab and
// its "Auth Enabled" line are gone with the tabs.
vi.mock(
  "ag-grid-react",
  async () => await import("../providers/__tests__/grid-mock"),
);

// The budgets switch is Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: mockOrgLevel,
    currentOrganizationId: "org-1",
  }),
}));

let mockOrgLevel = 8;
let mockConfig;
const mockPatch = vi.fn();
const mockSetBudget = vi.fn();
vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: { ...mockConfig, organization: "org-1" },
    isLoading: false,
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useConfigPatch", async (orig) => ({
  ...(await orig()),
  useConfigPatch: () => ({ mutate: mockPatch, isPending: false }),
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));
// Admission caps and history are separate journeys; use the real org editor and Budgets tab.
vi.mock("./admission/AdmissionCapEditor", () => ({ default: () => null }));
vi.mock("../settings/ConfigHistoryDrawer", () => ({ default: () => null }));
let mockSpend;
const mockUpdate = vi.fn();
const mockSpendCalls = [];

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ isLoading: false, data: mockConfig }),
  useRemoveBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useSetBudget: () => ({ mutate: mockSetBudget, isPending: false }),
  useProviderHealth: () => ({ data: { providers: [] } }),
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
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/gateway/settings"
          element={
            <>
              <OrgConfigSection />
              <Link to="/dashboard/gateway/budgets?org=org-1&gateway=default">
                Return to budgets
              </Link>
            </>
          }
        />
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

describe("BudgetRBACSection (R7-BUDGET)", () => {
  beforeEach(() => {
    mockUpdate.mockClear();
    mockSetBudget.mockClear();
    mockOrgLevel = 8;
    mockPatch.mockReset().mockImplementation(({ patch }, options) => {
      mockConfig = {
        ...mockConfig,
        budgets: { ...mockConfig.budgets, ...patch.budgets },
      };
      options.onSuccess({ gateway_synced: true });
    });
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

  it.each(["Change period", "Manage scopes", "Edit in Org config"])(
    "%s opens the real editor on Budgets without losing org or gateway",
    async (link) => {
      const user = userEvent.setup();
      mockConfig.budgets.teams = { eng: { limit: 25 } };
      renderTab("/dashboard/gateway/budgets?org=org-1&gateway=default");
      await user.click(screen.getByRole("link", { name: link }));
      expect(new URLSearchParams(window.location.search).get("org")).toBe(
        "org-1",
      );
      expect(new URLSearchParams(window.location.search).get("gateway")).toBe(
        "default",
      );
      expect(new URLSearchParams(window.location.search).get("section")).toBe(
        "budgets",
      );
      expect(screen.getByRole("tab", { name: "Budgets" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.getByLabelText("Budget Period")).toHaveTextContent(
        "Monthly",
      );
      expect(mockPatch).not.toHaveBeenCalled();
    },
  );

  it("the empty page offers scope setup directly", async () => {
    mockConfig.budgets = {};
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByRole("link", { name: "Manage scopes" }));
    expect(screen.getByRole("tab", { name: "Budgets" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("a mocked existing save updates daily spend and period after returning", async () => {
    const user = userEvent.setup();
    renderTab("/dashboard/gateway/budgets?org=org-1&gateway=default");
    await user.click(screen.getByRole("link", { name: "Change period" }));
    await user.click(screen.getByLabelText("Budget Period"));
    await user.click(screen.getByRole("option", { name: "Daily" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    expect(mockPatch).toHaveBeenCalledTimes(1);
    expect(mockPatch.mock.calls[0][0]).toMatchObject({
      gatewayId: "default",
      patch: { budgets: { org_period: "daily" } },
    });
    expect(mockPatch.mock.calls[0][0].patch.budgets).not.toHaveProperty(
      "org_limit",
    );
    await user.click(
      await screen.findByRole("link", { name: "Return to budgets" }),
    );
    expect(screen.getByText("Daily")).toBeInTheDocument();
    expect(screen.getByText("$3.00 / $100.00")).toBeInTheDocument();
    expect(mockSpendCalls.at(-1)).toEqual(["daily"]);
  });

  it("scoped budget readers open a read-only editor and cannot save", async () => {
    mockOrgLevel = 1;
    mockConfig.budgets.teams = { eng: { limit: 25 } };
    const user = userEvent.setup();
    renderTab();
    expect(screen.getByRole("button", { name: "Set budget" })).toBeDisabled();
    expect(
      screen.queryByRole("link", { name: "Edit in Org config" }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("link", { name: "View in Org config" }),
    );
    expect(screen.getByRole("tab", { name: "Budgets" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Save & Activate" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("checkbox", { name: "Enable per-org budgets" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("passes the effective blocking entry into the alternate budget preview", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(screen.getByRole("button", { name: "Set budget" }));
    await user.click(screen.getByLabelText(/Budget Level/));
    await user.click(screen.getByRole("option", { name: "Hard Limit" }));
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "25");
    expect(
      screen.getByText(/replaces the Organization budget.*Block/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Budget preview")).toHaveTextContent(
      /allows requests.*logs warnings/i,
    );
    expect(mockSetBudget).not.toHaveBeenCalled();
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
      screen.getAllByText(/is counted by the gateway/i).length,
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
    expect(
      screen.queryByText(/Enforcement unknown/i),
    ).not.toBeInTheDocument();
  });
});
