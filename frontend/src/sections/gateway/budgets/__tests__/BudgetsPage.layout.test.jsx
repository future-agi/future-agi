import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import BudgetRBACSection from "../BudgetRBACSection";
import { faTestTheme } from "../../__tests__/faTheme";
import { lastGrid } from "../../providers/__tests__/grid-mock";

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

const levels = () => screen.getByRole("region", { name: "Budget levels" });
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

const follows = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("Budgets page layout", () => {
  beforeEach(() => {
    state.config = {
      budgets: DEMO_BUDGETS,
      org_sections_enforced: ["budgets"],
    };
    state.orgLevel = 15;
    state.spend = { monthly: { total_spend: 12.5 } };
  });

  it("opens with a one-line purpose under the title", () => {
    renderPage();
    expect(
      screen.getByText(
        "Spending limits and what the gateway does when one is reached.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the table, its count, Change period and Manage scopes in one Budget levels section", () => {
    renderPage();
    const section = levels();
    expect(within(section).getByTestId("ag-grid")).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: "Change period" }),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: "Manage scopes" }),
    ).toBeInTheDocument();
    expect(
      within(section).getByTestId("budget-levels-count"),
    ).toHaveTextContent("3");
    expect(
      within(section).getByText(
        "Periods and team, user, API key and tag budgets open in Org config.",
      ),
    ).toBeInTheDocument();
  });

  it("states the organization period and when it resets, in UTC", () => {
    renderPage();
    expect(
      within(levels()).getByText(
        "Organization period: Monthly. Resets on the 1st, 00:00 UTC.",
      ),
    ).toBeInTheDocument();
  });

  it("states a daily organization period's reset", () => {
    state.config = {
      budgets: { ...DEMO_BUDGETS, org_period: "daily" },
      org_sections_enforced: ["budgets"],
    };
    state.spend = { daily: { total_spend: 1 } };
    renderPage();
    expect(
      within(levels()).getByText(
        "Organization period: Daily. Resets at 00:00 UTC.",
      ),
    ).toBeInTheDocument();
  });

  it("shows each level's period with when it resets", () => {
    renderPage();
    const org = cell("org_limit", "Period");
    expect(within(org).getByText("Monthly")).toBeInTheDocument();
    expect(
      within(org).getByText("Resets on the 1st, 00:00 UTC"),
    ).toBeInTheDocument();
    const tag = cell("tags:workload:batch-eval", "Period");
    expect(within(tag).getByText("Daily")).toBeInTheDocument();
    expect(within(tag).getByText("Resets at 00:00 UTC")).toBeInTheDocument();
  });

  it("shows how much of an organization limit is used as text beside the bar", () => {
    renderPage();
    const used = cell("org_limit", "Spent / limit");
    expect(within(used).getByText("$12.50 / $25.00")).toBeInTheDocument();
    expect(within(used).getByText("50% used")).toBeInTheDocument();
  });

  it("shows when Set budget last saved a level, in UTC, and a dash for levels set in Org config", () => {
    renderPage();
    const saved = cell("org_limit", "Last saved");
    expect(within(saved).getByText("Oct 21, 2026")).toBeInTheDocument();
    expect(within(saved).getByText("08:00 UTC")).toBeInTheDocument();
    expect(cell("keys:support-bot", "Last saved")).toHaveTextContent(/^—$/);
  });

  it("gives a level that is not in force a taller row, so its warning shows in full", () => {
    state.config = {
      budgets: {
        ...DEMO_BUDGETS,
        hard_limit: {
          limit: 50,
          on_exceed: "block",
          saved_at: "2026-10-22T08:00:00.000Z",
        },
      },
      org_sections_enforced: ["budgets"],
    };
    renderPage();
    const warning = within(row("org_limit")).getByText(
      "Not in force: the later Hard Limit save is enforced instead.",
    );
    expect(warning).not.toHaveClass("MuiTypography-noWrap");
    const { getRowHeight, rowData } = lastGrid.props;
    const org = rowData.find((r) => r.id === "org_limit");
    const hard = rowData.find((r) => r.id === "hard_limit");
    expect(getRowHeight({ data: org })).toBeGreaterThan(
      getRowHeight({ data: hard }),
    );
  });

  it("puts the notes on where spend comes from under the table, in the same section", () => {
    renderPage();
    const section = levels();
    const grid = within(section).getByTestId("ag-grid");
    const orgNote = within(section).getByText(
      /checks each limit against its own count of this spend/i,
    );
    const otherNote = within(section).getByText(
      /this page shows organization spend only/i,
    );
    expect(follows(grid, orgNote)).toBe(true);
    expect(follows(grid, otherNote)).toBe(true);
  });

  it("shows the last 30 days of spend after the budgets, with where per-request costs are", () => {
    renderPage();
    const spend = screen.getByRole("region", { name: "Last 30 days" });
    expect(within(spend).getByText("Spent")).toBeInTheDocument();
    expect(within(spend).getByText("Requests")).toBeInTheDocument();
    expect(within(spend).getByText("603")).toBeInTheDocument();
    expect(within(spend).getByRole("link", { name: "Logs" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/logs",
    );
    // The number of budgets is the Budget levels section's count.
    expect(within(spend).queryByText("Budgets")).toBeNull();
    expect(follows(levels(), spend)).toBe(true);
  });

  it("shows a compact empty state in the Budget levels section that names both next steps", () => {
    state.config = { budgets: {} };
    renderPage();
    const section = levels();
    expect(within(section).getByText("No budgets yet")).toBeInTheDocument();
    expect(
      within(section).getByText(
        "Use Set budget above to add an organization limit. Manage scopes adds team, user, API key and tag budgets in Org config.",
      ),
    ).toBeInTheDocument();
    // One primary action: the header's Set budget, which the text names.
    expect(screen.getAllByRole("button", { name: "Set budget" })).toHaveLength(
      1,
    );
    expect(
      within(section).queryByRole("button", { name: "Set budget" }),
    ).toBeNull();
    expect(
      within(section).getByRole("link", { name: "Manage scopes" }),
    ).toBeInTheDocument();
    expect(
      within(section).getByTestId("budget-levels-count"),
    ).toHaveTextContent("0");
  });
});
