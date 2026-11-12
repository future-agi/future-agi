import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import BudgetRBACSection from "../BudgetRBACSection";
import { faTestTheme } from "../../__tests__/faTheme";
import { lastGrid } from "../../providers/__tests__/grid-mock";
import { expectAdminOnly, expectEditable } from "../../testing/adminOnly";

vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);

const state = vi.hoisted(() => ({
  config: null,
  configError: null,
  configLoading: false,
  orgLevel: 15,
  spend: {},
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: state.config,
    isLoading: state.configLoading,
    error: state.configError,
    refetch: vi.fn(),
  }),
  useRemoveBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useSetBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useBudgetSpend", () => ({
  useBudgetSpend: () => ({
    data: state.spend,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: {
      total_cost: { value: "0.087701" },
      total_requests: { value: 603 },
    },
    isLoading: false,
  }),
}));

const DEMO_BUDGETS = {
  enabled: true,
  default_period: "monthly",
  org_limit: {
    limit: 25,
    on_exceed: "throttle",
    throttle_rpm: 4,
    alert_threshold: 80,
    saved_at: "2026-10-21T08:00:00.000Z",
  },
  keys: { "support-bot": { hard: false, limit: 5, period: "monthly" } },
  tags: {
    "workload:batch-eval": { hard: true, limit: 0.0004, period: "daily" },
  },
};

function renderPage() {
  return render(
    <HelmetProvider>
      <BudgetRBACSection />
    </HelmetProvider>,
    { theme: faTestTheme("light") },
  );
}

const row = (id) => screen.getByTestId(`budget-row-${id}`).closest("tr");

describe("Budgets page", () => {
  beforeEach(() => {
    state.config = {
      budgets: DEMO_BUDGETS,
      org_sections_enforced: ["budgets"],
    };
    state.configError = null;
    state.configLoading = false;
    state.orgLevel = 15;
    state.spend = { monthly: { total_spend: 12.5 } };
  });

  it("is one view: an 15 px title, the budgets grid and no tabs", () => {
    renderPage();
    const title = screen.getByRole("heading", { level: 1, name: "Budgets" });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.getByTestId("ag-grid")).toBeInTheDocument();
    expect(screen.queryByText(/Auth Enabled/i)).toBeNull();
  });

  it("lists every level, with the org spend of its period against its limit", () => {
    renderPage();
    expect(lastGrid.props.rowData.map((r) => r.label)).toEqual([
      "Organization",
      "Key support-bot",
      "Tag workload:batch-eval",
    ]);
    expect(
      within(row("org_limit")).getByText("$12.50 / $25.00"),
    ).toBeInTheDocument();
    expect(
      within(row("keys:support-bot")).getByText(/Counted by the gateway/),
    ).toBeInTheDocument();
  });

  // The gateway runs Throttle with its rate and pushes the alert
  // threshold, so both are shown as set, with no "not enforced" note.
  it("shows a throttle with its rate per gateway replica and the alert threshold as set", () => {
    renderPage();
    const org = within(row("org_limit"));
    expect(org.getByText("Throttles")).toBeInTheDocument();
    expect(
      org.getByText("4 requests a minute per gateway replica past the limit"),
    ).toBeInTheDocument();
    expect(org.getByText("80%")).toBeInTheDocument();
    expect(screen.queryByText(/warns only/i)).toBeNull();
    expect(screen.queryByText(/not sent to the gateway/i)).toBeNull();
    expect(screen.queryByText(/no throttle action/i)).toBeNull();
  });

  it("sends team, user, key and tag levels to Org config, where they are edited", () => {
    renderPage();
    expect(
      within(row("tags:workload:batch-eval")).getByRole("link", {
        name: "Edit in Org config",
      }),
    ).toHaveAttribute("href", "/dashboard/gateway/settings?tab=org-config");
  });

  // Purple (color="primary") at the native 38 px, as on
  // Datasets, Keys, Members and Evals; the theme default draws it black.
  it("draws Set budget as the Future AGI primary button at the native size", () => {
    renderPage();
    const cta = screen.getAllByRole("button", { name: "Set budget" })[0];
    expect(cta).toHaveClass("MuiButton-containedPrimary");
    expect(cta).toHaveClass("MuiButton-sizeMedium");
  });

  it("shows the one empty state with Set budget when there are no budgets", () => {
    state.config = { budgets: {} };
    renderPage();
    expect(screen.getByText("No budgets yet")).toBeInTheDocument();
  });

  it("shows the one error state with Retry when the config cannot be read", () => {
    state.config = null;
    state.configError = new Error("boom");
    renderPage();
    expect(screen.getByText("Couldn't load your budgets")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("shows a member the switch, Set budget and the row actions disabled, saying why", () => {
    state.orgLevel = 3;
    renderPage();
    expectAdminOnly(screen.getByRole("checkbox", { name: /Enforce budgets/i }));
    expectAdminOnly(screen.getByRole("button", { name: "Set budget" }));
    expectAdminOnly(
      within(row("org_limit")).getByRole("button", {
        name: "Remove budget org_limit",
      }),
    );
  });

  it("lets an admin set, edit and remove", () => {
    state.orgLevel = 8;
    renderPage();
    expectEditable(screen.getByRole("button", { name: "Set budget" }));
    expectEditable(
      within(row("org_limit")).getByRole("button", {
        name: "Edit budget org_limit",
      }),
    );
  });
});
