import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import UsageSummary from "src/pages/dashboard/settings/UsageSummary/UsageSummary";

import GatewayUsageSplit from "../GatewayUsageSplit";
import { splitGatewayUsage } from "../gateway-usage";

const COSTS = {
  group_by: "billing_mode",
  total_cost: "0.087686",
  breakdown: [
    { name: "Own key", total_cost: "0.087148" },
    { name: "Future AGI credits", total_cost: "0.000538" },
  ],
};

const STATEMENT = {
  month: "2026-10",
  spend_by_model: [
    {
      model: "openai/gpt-4o",
      count: 6,
      spend_usd: 0.000552,
      upstream_usd: 0.000528,
      markup_usd: 0.000024,
    },
    {
      model: "mistralai/ministral-8b-2512",
      count: 5,
      spend_usd: 0.00001,
      upstream_usd: 0.00001,
      markup_usd: 0,
    },
  ],
};

// These cases cover central billing once the gateway's credit ledger is
// served; on this backend it is hidden (page-status.js
// HIDDEN_GATEWAY_CONTROLS.managedCredits, see credits-hidden.test.jsx).
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayControlHidden: () => false,
}));

const state = vi.hoisted(() => ({
  isCloud: false,
  credits: true,
  enabled: true,
  calls: [],
  statement: undefined,
  costs: undefined,
  statementError: false,
  costError: false,
  pending: 0,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: state.isCloud, isLoading: false }),
}));
vi.mock("src/sections/settings/UsageSummaryV2/UsageSummaryV2", () => ({
  default: () => <div>Platform usage</div>,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url, config = {}) => {
        state.calls.push([url, config.params]);
        if (url === gw.managedCredits) {
          if (!state.credits) throw new Error("billing_unavailable");
          return {
            data: {
              result: {
                enabled: state.enabled,
                available_usd: 1,
                granted_usd: 2,
                settled_spend_usd: 1,
                pending_settlement_usd: state.pending,
                last_reconciled_at: "2026-10-28T00:00:00Z",
              },
            },
          };
        }
        if (url === gw.managedCreditStatement) {
          if (state.statementError) throw new Error("statement_unavailable");
          return { data: { result: state.statement } };
        }
        if (url === gw.analyticsCost) {
          if (state.costError) throw new Error("analytics_unavailable");
          return {
            data: { result: state.costs },
          };
        }
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

function Where({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
Where.propTypes = { into: PropTypes.object.isRequired };

function renderUi(ui) {
  const where = {};
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={["/dashboard/settings/usage-summary"]}>
            {ui}
            <Where into={where} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return where;
}

describe("managed vs own-key split", () => {
  it("takes managed spend from the ledger and own-key cost from the logs", () => {
    expect(splitGatewayUsage(STATEMENT, COSTS)).toEqual({
      managedSpend: 0.000562,
      managedRequests: 11,
      ownKeyCost: 0.087148,
    });
  });

  it("never subtracts credit spend and treats unavailable analytics as unknown", () => {
    expect(
      splitGatewayUsage(STATEMENT, { group_by: "billing_mode", breakdown: [] })
        .ownKeyCost,
    ).toBe(0);
    expect(
      splitGatewayUsage(STATEMENT, { group_by: "provider", total_cost: "999" })
        .ownKeyCost,
    ).toBeNull();
    expect(splitGatewayUsage(null, null)).toEqual({
      managedSpend: null,
      managedRequests: null,
      ownKeyCost: null,
    });
  });

  it.each([null, undefined])(
    "H7-C25-002 treats an absent statement (%s) as unknown, not zero",
    (statement) => {
      expect(splitGatewayUsage(statement, COSTS)).toEqual({
        managedSpend: null,
        managedRequests: null,
        ownKeyCost: 0.087148,
      });
    },
  );

  it("H7-C25-002 preserves a successfully empty ledger as real zero spend", () => {
    expect(splitGatewayUsage({ spend_by_model: [] }, COSTS)).toEqual({
      managedSpend: 0,
      managedRequests: 0,
      ownKeyCost: 0.087148,
    });
  });
});

describe("AI Gateway usage in Usage Summary", () => {
  beforeEach(() => {
    state.isCloud = false;
    state.credits = true;
    state.enabled = true;
    state.calls = [];
    state.statement = STATEMENT;
    state.costs = COSTS;
    state.statementError = false;
    state.costError = false;
    state.pending = 0;
  });

  it.each(["error", "null result", "missing result"])(
    "H7-C25-002 shows unavailable managed spend beside $12 own-key cost on statement %s and retries only the statement",
    async (failure) => {
      state.statementError = failure === "error";
      state.statement = failure === "missing result" ? undefined : null;
      state.costs = {
        group_by: "billing_mode",
        breakdown: [{ name: "Own key", total_cost: "12" }],
      };
      renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
      expect(
        await screen.findByText("Managed spend unavailable"),
      ).toBeInTheDocument();
      expect(screen.getByTitle("12 USD")).toHaveTextContent("$12");
      expect(screen.queryByText("$0")).not.toBeInTheDocument();
      expect(screen.queryByText(/0 requests/)).not.toBeInTheDocument();
      expect(screen.queryByRole("img")).not.toBeInTheDocument();

      state.statementError = false;
      state.statement = { spend_by_model: [{ spend_usd: 25, count: 4 }] };
      fireEvent.click(
        screen.getByRole("button", { name: "Retry managed spend" }),
      );
      expect(await screen.findByTitle("25 USD")).toHaveTextContent("$25");
      expect(screen.getByText(/4 requests/)).toBeInTheDocument();
      expect(
        screen.queryByText("Managed spend unavailable"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("img", {
          name: "Future AGI credits 68% of gateway spend",
        }),
      ).toBeInTheDocument();
      expect(
        state.calls.filter(
          ([url]) => url === "/agentcc/managed-credits/statement/",
        ),
      ).toHaveLength(2);
      expect(
        state.calls.filter(
          ([url]) => url === "/agentcc/analytics/cost-breakdown/",
        ),
      ).toHaveLength(1);
    },
  );

  it("H7-C25-002 renders the successful $25 ledger and four managed requests", async () => {
    state.statement = { spend_by_model: [{ spend_usd: 25, count: 4 }] };
    renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
    expect(await screen.findByTitle("25 USD")).toHaveTextContent("$25");
    expect(screen.getByText(/4 requests/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry managed spend" }),
    ).not.toBeInTheDocument();
  });

  it("H7-C25-002 renders real zero spend only for a successful empty statement", async () => {
    state.statement = { spend_by_model: [] };
    renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
    expect(await screen.findByTitle("0 USD")).toHaveTextContent("$0");
    expect(screen.getByText(/0 requests/)).toBeInTheDocument();
    expect(
      screen.queryByText("Managed spend unavailable"),
    ).not.toBeInTheDocument();
  });

  // After two managed calls this card names, under its figure and in
  // Billing's words, the spend charged at the gateway and not yet in the
  // ledger, so it agrees with Billing's Spent, settling $0.000184. It is
  // today's figure, so only the current month shows it; a past month's
  // ledger spend is unchanged.
  it("names spend still settling under a $0.00 Future AGI credits figure, in the current month only", async () => {
    state.statement = { spend_by_model: [] };
    state.pending = 0.000184;
    renderUi(<GatewayUsageSplit now={new Date("2026-11-11T12:00:00Z")} />);
    const section = await screen.findByRole("region", {
      name: "AI Gateway usage",
    });
    expect(await within(section).findByTitle("0 USD")).toHaveTextContent(
      /^\$0\.00$/,
    );
    expect(
      await within(section).findByText(
        "+ $0.000184 settling: charged at the gateway, not yet in the ledger",
      ),
    ).toBeInTheDocument();
    expect(within(section).getByText(/0 requests/)).toBeInTheDocument();

    await userEvent.click(
      within(section).getByRole("combobox", { name: "Month" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "October 2026" }));
    await waitFor(() =>
      expect(state.calls).toContainEqual([
        "/agentcc/managed-credits/statement/",
        { month: "2026-10" },
      ]),
    );
    expect(await within(section).findByTitle("0 USD")).toBeInTheDocument();
    expect(within(section).queryByText(/settling/)).not.toBeInTheDocument();
  });

  it("H7-C25-002 leaves own-key-only failures unchanged and keeps valid managed spend", async () => {
    state.costError = true;
    state.statement = { spend_by_model: [{ spend_usd: 25, count: 4 }] };
    renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
    expect(await screen.findByTitle("25 USD")).toHaveTextContent("$25");
    expect(screen.getByText(/4 requests/)).toBeInTheDocument();
    expect(screen.queryByText("Own key")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Managed spend unavailable"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry managed spend" }),
    ).not.toBeInTheDocument();
  });

  it("H7-C25-002 keeps simultaneous query failures independent", async () => {
    state.statementError = true;
    state.costError = true;
    renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
    expect(
      await screen.findByText("Managed spend unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Own key")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    state.statementError = false;
    state.statement = { spend_by_model: [{ spend_usd: 25, count: 4 }] };
    fireEvent.click(
      screen.getByRole("button", { name: "Retry managed spend" }),
    );
    expect(await screen.findByTitle("25 USD")).toHaveTextContent("$25");
    expect(screen.queryByText("Own key")).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(
      state.calls.filter(
        ([url]) => url === "/agentcc/analytics/cost-breakdown/",
      ),
    ).toHaveLength(1);
  });

  it("shows both sides of the month and says how own-key cost is worked out", async () => {
    renderUi(<GatewayUsageSplit now={new Date("2026-10-20T12:00:00Z")} />);
    const section = await screen.findByRole("region", {
      name: "AI Gateway usage",
    });
    // formatAmount keeps sub-dollar amounts exact.
    await waitFor(() =>
      expect(
        within(section).getByText("Future AGI credits").nextSibling,
      ).toHaveTextContent("$0.000562"),
    );
    expect(within(section).getByTitle("0.000562 USD")).toHaveTextContent(
      "$0.000562",
    );
    expect(within(section).getByText("Own key").nextSibling).toHaveTextContent(
      "$0.087148",
    );
    expect(within(section).getByTitle("0.087148 USD")).toHaveTextContent(
      "$0.087148",
    );
    expect(within(section).getByText(/11 requests/)).toBeInTheDocument();
    expect(
      within(section).getByText(
        /logged for requests on your own provider keys/,
      ),
    ).toBeInTheDocument();
    // Credits spend is what was charged; the copy names no markup.
    expect(
      within(section).getByText(/the amounts your requests were charged/),
    ).toBeInTheDocument();
    expect(section).not.toHaveTextContent(/markup|%/i);
    expect(
      within(section).getByRole("link", { name: "Ledger and statement" }),
    ).toHaveAttribute("href", "/dashboard/settings/billing#ai-gateway-credits");
    const cost = state.calls.find(
      ([url]) => url === "/agentcc/analytics/cost-breakdown/",
    );
    expect(cost[1]).toEqual({
      start: "2026-10-01T00:00:00.000Z",
      end: "2026-11-01T00:00:00.000Z",
      group_by: "billing_mode",
    });
  });

  it("is the Usage Summary page off cloud when gateway credits exist", async () => {
    renderUi(<UsageSummary />);
    expect(
      await screen.findByRole("region", { name: "AI Gateway usage" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Platform usage")).not.toBeInTheDocument();
  });

  it("follows the platform usage on cloud", async () => {
    state.isCloud = true;
    renderUi(<UsageSummary />);
    expect(await screen.findByText("Platform usage")).toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "AI Gateway usage" }),
    ).toBeInTheDocument();
  });

  it.each([true, false])(
    "F4 keeps the disabled organization's usage split dark despite history (hosted: %s)",
    async (isCloud) => {
      state.isCloud = isCloud;
      state.enabled = false;
      const where = renderUi(<UsageSummary />);
      if (isCloud) {
        expect(await screen.findByText("Platform usage")).toBeInTheDocument();
        expect(where.pathname).toBe("/dashboard/settings/usage-summary");
      } else {
        await waitFor(() =>
          expect(where.pathname).toBe("/dashboard/settings/org-settings"),
        );
        expect(screen.queryByText("Platform usage")).toBeNull();
      }
      expect(
        screen.queryByRole("region", { name: "AI Gateway usage" }),
      ).toBeNull();
      expect(
        state.calls.some(
          ([url]) => url === "/agentcc/managed-credits/statement/",
        ),
      ).toBe(false);
    },
  );

  it("sends the user to Org Settings with neither", async () => {
    state.credits = false;
    const where = renderUi(<UsageSummary />);
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/settings/org-settings"),
    );
  });
});
