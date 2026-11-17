// The Spent / limit cell gives the amount pair its own line, so a sub-cent
// spend and limit both read in full; the share used sits beside the bar, and
// the pair is also on hover.
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import BudgetRBACSection from "../BudgetRBACSection";
import { formatCost, formatCostUpperBound } from "../../utils/formatters";
import { faTestTheme } from "../../__tests__/faTheme";

vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);

const state = vi.hoisted(() => ({
  config: null,
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
    isLoading: false,
    error: null,
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

// The cell of a row under the column with this header (the grid stand-in
// renders the columns in order).
function cell(id, header) {
  const headers = within(screen.getByTestId("ag-grid"))
    .getAllByRole("columnheader")
    .map((th) => th.textContent);
  const index = headers.indexOf(header);
  expect(index).toBeGreaterThanOrEqual(0);
  return row(id).querySelectorAll("td")[index];
}

// The walk's amounts: a sub-cent organization limit already passed.
const SPENT = 0.000528;
const LIMIT = 0.000352;

describe("Budgets: the Spent / limit cell", () => {
  beforeEach(() => {
    state.config = {
      budgets: {
        ...DEMO_BUDGETS,
        org_limit: { ...DEMO_BUDGETS.org_limit, limit: LIMIT },
      },
      org_sections_enforced: ["budgets"],
    };
    state.orgLevel = 15;
    state.spend = { monthly: { total_spend: SPENT } };
  });

  it("puts the spent and limit amounts on their own line, with the pair on hover", () => {
    renderPage();
    const pair = `${formatCost(SPENT)} / ${formatCostUpperBound(LIMIT)}`;
    const amounts = within(cell("org_limit", "Spent / limit")).getByText(pair);
    expect(amounts).toHaveAttribute("title", pair);
  });

  it("shows the share used beside the bar, not beside the amounts", () => {
    renderPage();
    const used = cell("org_limit", "Spent / limit");
    const share = within(used).getByText(/^\d+% used$/);
    expect(share.parentElement).toContainElement(
      within(used).getByRole("progressbar"),
    );
  });
});
