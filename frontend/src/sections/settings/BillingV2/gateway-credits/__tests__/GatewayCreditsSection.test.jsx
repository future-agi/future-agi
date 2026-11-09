import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { useGatewayCreditSummary } from "../api";
import GatewayCreditsSection from "../GatewayCreditsSection";
import { formatAmount } from "../format";

const SUMMARY = {
  enabled: true,
  granted_usd: 35,
  settled_spend_usd: 0.000664,
  available_usd: 34.999336,
  gateway: { spent_usd: 8.1, held_usd: 0.4, available_usd: 26.5 },
  gateway_warning: null,
  pending_settlement_usd: 0,
  markup_percent: 5,
  min_topup_usd: 25,
  low_balance_threshold_usd: null,
  auto_reload: {
    enabled: false,
    threshold_usd: null,
    amount_usd: null,
    disabled_reason: "",
  },
  last_reconciled_at: null,
  billing_available: false,
  currency: "USD",
};
const ENTRIES = [
  {
    id: "e1",
    occurred_at: "2026-10-20T10:10:05Z",
    kind: "spend",
    amount_usd: -0.000002,
    request_id: "01M5",
    model: "mistralai/ministral-8b-2512",
    provider: "openrouter",
    markup_usd: 0,
    cost_source: "catalog_max",
    stripe_ref: "",
  },
  {
    id: "e0",
    occurred_at: "2026-10-20T02:30:00Z",
    kind: "grant",
    amount_usd: 25,
    request_id: "",
    model: "",
    provider: "",
    markup_usd: null,
    cost_source: "",
    stripe_ref: "",
  },
];
const STATEMENT = {
  month: "2026-10",
  currency: "USD",
  opening_balance: 0,
  credits_by_kind: { topup: 0, grant: 35, refund: 0, adjustment: 0, expiry: 0 },
  spend_by_model: [
    {
      model: "openai/gpt-4o",
      count: 6,
      spend_usd: 0.000552,
      markup_usd: 0.000024,
    },
  ],
  closing_balance: 34.999336,
};

const statementOverride = vi.hoisted(() => ({ value: null }));
const api = vi.hoisted(() => ({
  summary: null,
  entries: null,
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  status: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  api.get.mockImplementation(async (url, config = {}) => {
    if (url === gw.managedCredits) {
      if (api.summary instanceof Error) throw api.summary;
      return { data: { result: api.summary } };
    }
    if (url === gw.managedCreditEntries) {
      return { data: { result: api.entries(config.params) } };
    }
    if (url === gw.managedCreditStatement) {
      if (statementOverride.value instanceof Error)
        throw statementOverride.value;
      return { data: { result: statementOverride.value || STATEMENT } };
    }
    if (url.startsWith("/agentcc/managed-credits/checkout/")) {
      return { data: { result: await api.status(url) } };
    }
    throw new Error(`unmocked GET ${url}`);
  });
  return {
    ...actual,
    default: {
      ...actual.default,
      get: api.get,
      post: api.post,
      put: api.put,
    },
  };
});
const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import(
    "src/sections/gateway/mcp/testing/agGridMock"
  );
  return {
    AgGridReact: forwardRef(function MockGrid(props, _ref) {
      grid.props = props;
      return <MockAgGrid {...props} />;
    }),
  };
});
const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

function Where({ into }) {
  const location = useLocation();
  into.search = location.search;
  into.hash = location.hash;
  return null;
}
Where.propTypes = { into: PropTypes.object.isRequired };

// Observe the same summary as the section so negative assertions wait for
// React to render settled data, not the initial loading/null branch.
function SummaryReadState() {
  const { isFetching, isSuccess, isError } = useGatewayCreditSummary();
  return (
    <output data-testid="credit-summary-state">
      {isFetching
        ? "loading"
        : isSuccess
          ? "settled"
          : isError
            ? "error"
            : "loading"}
    </output>
  );
}
const waitForSummary = (state = "settled") =>
  waitFor(() =>
    expect(screen.getByTestId("credit-summary-state")).toHaveTextContent(state),
  );

function renderSection(url = "/dashboard/settings/billing", mode = "light") {
  const where = {};
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider
        theme={createTheme({ palette: palette(mode), typography })}
      >
        <MemoryRouter initialEntries={[url]}>
          <GatewayCreditsSection />
          <SummaryReadState />
          <Where into={where} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return where;
}

describe("AI Gateway credits in central Billing", () => {
  beforeEach(() => {
    api.summary = { ...SUMMARY };
    api.entries = vi.fn(() => ({ entries: ENTRIES, next_cursor: null }));
    api.post.mockReset();
    api.put.mockReset();
    api.status.mockReset();
    snackbars.length = 0;
    grid.props = null;
    statementOverride.value = null;
  });
  afterEach(() => vi.useRealTimers());

  it("F4 renders no new wallet for an org disabled with history and billing available", async () => {
    api.summary = {
      ...SUMMARY,
      enabled: false,
      billing_available: true,
      last_reconciled_at: "2026-10-28T00:00:00Z",
    };
    renderSection();
    await waitForSummary();
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Add credits" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Auto-reload" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Credit ledger" })).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("shows the ledger balance with granted, spent and the markup", async () => {
    api.entries = vi.fn(() => ({
      entries: [
        { ...ENTRIES[0], request_id: "req_abc" },
        { ...ENTRIES[1], kind: "topup" },
      ],
      next_cursor: null,
    }));
    renderSection();
    const section = await screen.findByRole("region", {
      name: "AI Gateway credits",
    });
    expect(section).toHaveAttribute("id", "ai-gateway-credits");
    expect(
      (await within(section).findByText("Available")).nextSibling,
    ).toHaveTextContent("$34.999336");
    const figure = (label) => within(section).getByText(label).nextSibling;
    expect(figure("Granted")).toHaveTextContent("$35.00");
    // Ruling 528(1), a68e08f72c: ledger amounts stay exact below a dollar.
    expect(figure("Spent")).toHaveTextContent("$0.000664");
    expect(figure("Spent")).toHaveAttribute("title", "0.000664 USD");
    expect(within(section).getByText(/5% markup/)).toBeInTheDocument();
    expect(
      within(section).getByText(
        /\$26\.50 available now, after \$0\.40 held by requests in flight/,
      ),
    ).toBeInTheDocument();
    const ledger = await screen.findByRole("region", { name: "Credit ledger" });
    expect(await within(ledger).findByText("req_abc")).toBeInTheDocument();
    expect(within(ledger).getByText("Top-up")).toBeInTheDocument();
    expect(
      grid.props.columnDefs.find((column) => column.field === "request_id"),
    ).toMatchObject({ headerName: "Request ID" });
  });

  // A build that sells no top-up
  // states no minimum top-up and never says credit was bought.
  it("offers no top-up where this build cannot sell credits", async () => {
    renderSection();
    await screen.findByText("$34.999336");
    expect(
      screen.queryByRole("button", { name: "Add credits" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Minimum top-up")).not.toBeInTheDocument();
    expect(screen.queryByText(/bought/i)).not.toBeInTheDocument();
  });

  it("states the minimum top-up and bought credit where it sells credit", async () => {
    api.summary = { ...SUMMARY, billing_available: true };
    renderSection();
    const section = await screen.findByRole("region", {
      name: "AI Gateway credits",
    });
    expect(
      (await within(section).findByText("Available")).nextSibling,
    ).toHaveTextContent("$34.999336");
    const figure = (label) => within(section).getByText(label).nextSibling;
    expect(figure("Granted and bought")).toHaveTextContent("$35.00");
    expect(figure("Minimum top-up")).toHaveTextContent(
      formatAmount(SUMMARY.min_topup_usd),
    );
  });

  it("S094 states credit eligibility and describes requesting checkout", async () => {
    api.summary = { ...SUMMARY, billing_available: true };
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Add credits" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Add credits" });
    expect(
      within(dialog).getByText(
        "Future AGI credits pay for credit-eligible catalog models through the AI Gateway. Select Continue to payment to request a checkout page.",
        { exact: true },
      ),
    ).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/with one key/i);
    expect(
      within(dialog).getByRole("button", { name: "Continue to payment" }),
    ).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("tops up through the existing checkout, returning to Billing", async () => {
    api.summary = { ...SUMMARY, billing_available: true };
    api.post.mockResolvedValue({
      data: {
        result: {
          checkout_url: "https://checkout.test/cs_1",
          session_id: "cs_1",
        },
      },
    });
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      assign,
      origin: "http://app.test",
    });
    try {
      renderSection();
      await userEvent.click(
        await screen.findByRole("button", { name: "Add credits" }),
      );
      const dialog = screen.getByRole("dialog", { name: "Add credits" });
      const amount = within(dialog).getByLabelText("Amount (USD)");
      expect(amount).toHaveValue(25);
      await userEvent.clear(amount);
      await userEvent.type(amount, "10");
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Continue to payment" }),
      );
      expect(
        within(dialog).getByText("The minimum top-up is $25.00."),
      ).toBeInTheDocument();
      expect(api.post).not.toHaveBeenCalled();

      await userEvent.clear(amount);
      await userEvent.type(amount, "50");
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Continue to payment" }),
      );
      await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
      const [url, body] = api.post.mock.calls[0];
      expect(url).toBe("/agentcc/managed-credits/checkout/");
      expect(body).toEqual({
        amount_usd: 50,
        success_url:
          "http://app.test/dashboard/settings/billing?topup=success&session_id={CHECKOUT_SESSION_ID}#ai-gateway-credits",
        cancel_url:
          "http://app.test/dashboard/settings/billing?topup=cancel#ai-gateway-credits",
      });
      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith("https://checkout.test/cs_1"),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("confirms a top-up when the checkout returns and the ledger has it", async () => {
    api.status
      .mockResolvedValueOnce({
        session_id: "cs_1",
        status: "pending",
        credited: false,
      })
      .mockResolvedValue({
        session_id: "cs_1",
        status: "complete",
        credited: true,
      });
    const where = renderSection(
      "/dashboard/settings/billing?topup=success&session_id=cs_1&x=1#ai-gateway-credits",
    );
    await waitFor(
      () =>
        expect(snackbars.map((s) => s.message)).toContain(
          "Credits added to your AI Gateway balance.",
        ),
      { timeout: 8000 },
    );
    expect(api.status).toHaveBeenCalledWith(
      "/agentcc/managed-credits/checkout/cs_1/",
    );
    await waitFor(() => expect(where.search).toBe("?x=1"));
  });

  it("lists the ledger and filters it by kind", async () => {
    renderSection();
    const ledger = await screen.findByRole("region", { name: "Credit ledger" });
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(2));
    expect(
      within(ledger).getByRole("combobox", { name: /Kind/ }),
    ).toHaveTextContent("All");
    await userEvent.click(
      within(ledger).getByRole("combobox", { name: /Kind/ }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Spend" }));
    await waitFor(() =>
      expect(api.entries).toHaveBeenLastCalledWith({
        limit: 50,
        kind: "spend",
      }),
    );
  });

  // The same charge read "$0.0000040" in the gateway's Logs
  // and "-$0.000004" here. Ledger cells now use the gateway's grid-cell rule.
  it("writes a sub-cent charge like the gateway's Logs cell, exact in its tooltip", async () => {
    renderSection();
    await screen.findByRole("region", { name: "Credit ledger" });
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(2));
    const money = ["amount_usd", "markup_usd"].map((field) =>
      grid.props.columnDefs.find((col) => col.field === field),
    );
    expect(grid.props.columnDefs.map((col) => col.field)).not.toContain(
      "upstream_usd",
    );
    expect(grid.props.columnDefs.map((col) => col.field)).not.toContain("note");
    for (const col of money) {
      expect(col.valueFormatter({ value: -0.000002 })).toBe("-$0.000002");
      expect(col.tooltipValueGetter({ value: -0.000002 })).toBeNull();
      expect(col.valueFormatter({ value: 25 })).toBe("$25.00");
      expect(col.tooltipValueGetter({ value: 25 })).toBeNull();
      expect(col.valueFormatter({ value: null })).toBe("—");
    }
  });

  // Every ledger charge is a managed
  // call, so its provider reads "Future AGI managed", never the upstream the
  // gateway bought it from. A grant or top-up has no provider.
  it("names the provider of a credit charge Future AGI", async () => {
    renderSection();
    await screen.findByRole("region", { name: "Credit ledger" });
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(2));
    const provider = grid.props.columnDefs.find(
      (col) => col.headerName === "Provider",
    );
    const shown = (data) => {
      const value = provider.valueGetter
        ? provider.valueGetter({ data })
        : data[provider.field];
      return provider.valueFormatter
        ? provider.valueFormatter({ value, data })
        : value;
    };
    expect(shown(ENTRIES[0])).toBe("Future AGI");
    expect(shown(ENTRIES[1])).toBe("");
  });

  it("loads the next page of the ledger", async () => {
    api.entries = vi.fn((params) =>
      params.cursor
        ? {
            entries: [{ ...ENTRIES[0], id: "e9", kind: "refund" }],
            next_cursor: null,
          }
        : { entries: ENTRIES, next_cursor: "c1" },
    );
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Load more" }),
    );
    await waitFor(() => expect(grid.props.rowData).toHaveLength(3));
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it("shows the month's statement by model", async () => {
    statementOverride.value = {
      ...STATEMENT,
      opening_balance: 10,
      closing_balance: 44.999336,
    };
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    expect(
      await within(statement).findByText("openai/gpt-4o"),
    ).toBeInTheDocument();
    expect(
      within(statement).getByText("Opening balance").closest("tr"),
    ).toHaveTextContent("$10.00");
    expect(
      within(statement).getByText("Grants").closest("tr"),
    ).toHaveTextContent("$35.00");
    expect(
      within(statement).getByText("Closing balance").closest("tr"),
    ).toHaveTextContent("$44.999336");
    const cells = within(
      within(statement).getByText("openai/gpt-4o").closest("tr"),
    ).getAllByRole("cell");
    expect(cells[1].textContent).toBe("6");
    expect(cells[2].textContent).toBe(formatAmount(-0.000552));
    expect(cells[3].textContent).toBe("$0.000024");
    const now = new Date();
    const previous = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1),
    );
    const month = previous.toISOString().slice(0, 7);
    const label = previous.toLocaleDateString("en-US", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    });
    api.get.mockClear();
    await userEvent.click(
      within(statement).getByRole("combobox", { name: "Month" }),
    );
    await userEvent.click(screen.getByRole("option", { name: label }));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/agentcc/managed-credits/statement/",
        { params: { month } },
      ),
    );
    expect(
      within(statement).getByRole("combobox", { name: "Month" }),
    ).toHaveTextContent(label);
    expect(api.post).not.toHaveBeenCalled();
  });

  // With the figures the ledger fixtures produce
  // (agentcc/tests/test_managed_credits_api.py TestBalanceAndSubCentMarkup).
  it("reads Available and the closing balance as one figure", async () => {
    api.summary = { ...SUMMARY, available_usd: 34.99942 };
    statementOverride.value = {
      ...STATEMENT,
      spend_by_model: [
        ...STATEMENT.spend_by_model,
        {
          model: "anthropic/claude-haiku-5.5",
          count: 7,
          spend_usd: 0.000028,
          markup_usd: 0,
        },
      ],
      closing_balance: 34.99942,
    };
    renderSection();
    const section = await screen.findByRole("region", {
      name: "AI Gateway credits",
    });
    expect(
      (await within(section).findByText("Available")).nextSibling,
    ).toHaveTextContent("$34.99942");
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    await within(statement).findByText("anthropic/claude-haiku-5.5");
    const closing = within(statement)
      .getByText("Closing balance")
      .closest("tr");
    expect(closing).toHaveTextContent("$34.99942");
    expect(closing).not.toHaveTextContent("$35.00");
  });

  it("shows the reported markup without inferring a rounding explanation from absent upstream data", async () => {
    statementOverride.value = {
      ...STATEMENT,
      spend_by_model: [
        ...STATEMENT.spend_by_model,
        {
          model: "anthropic/claude-haiku-5.5",
          count: 7,
          spend_usd: 0.000028,
          markup_usd: 0,
        },
        {
          model: "legacy/no-split",
          count: 1,
          spend_usd: 0.00004,
          markup_usd: null,
        },
      ],
    };
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    const row = (model) =>
      within(statement).getByText(model).closest("tr").textContent;
    await within(statement).findByText("legacy/no-split");
    expect(row("openai/gpt-4o")).toContain("$0.000024");
    expect(row("legacy/no-split")).toContain("—");
    expect(
      within(statement).queryByText(
        "Markup is rounded to the micro-dollar ($0.000001) on each request, so the smallest requests carry none.",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(statement).queryByRole("columnheader", { name: "Upstream" }),
    ).toBeNull();
    expect(row("anthropic/claude-haiku-5.5")).toContain("$0.00");
  });

  it("adds no markup note where managed calls carry no markup", async () => {
    api.summary = { ...SUMMARY, markup_percent: 0 };
    statementOverride.value = {
      ...STATEMENT,
      spend_by_model: [
        {
          model: "openai/gpt-4o",
          count: 6,
          spend_usd: 0.000528,
          markup_usd: 0,
        },
      ],
    };
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    await within(statement).findByText("openai/gpt-4o");
    const cells = within(
      within(statement).getByText("openai/gpt-4o").closest("tr"),
    ).getAllByRole("cell");
    expect(cells[3].textContent).toBe("$0.00");
    expect(
      within(statement).queryByText(/rounded to the micro-dollar/),
    ).not.toBeInTheDocument();
  });

  it("adds no markup note for a zero markup on requests too large to round it away", async () => {
    // 5%: a request rounds its markup to zero only under 10 micros.
    statementOverride.value = {
      ...STATEMENT,
      spend_by_model: [
        {
          model: "openai/gpt-4o",
          count: 2,
          spend_usd: 0.0002,
          markup_usd: 0,
        },
      ],
    };
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    await within(statement).findByText("openai/gpt-4o");
    const cells = within(
      within(statement).getByText("openai/gpt-4o").closest("tr"),
    ).getAllByRole("cell");
    expect(cells[3].textContent).toBe("$0.00");
    expect(
      within(statement).queryByText(/rounded to the micro-dollar/),
    ).not.toBeInTheDocument();
  });

  it("adds no markup note when every line carries its markup", async () => {
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    await within(statement).findByText("openai/gpt-4o");
    expect(
      within(statement).queryByText(/rounded to the micro-dollar/),
    ).not.toBeInTheDocument();
  });

  it("hides auto-reload where this build cannot charge for credit", async () => {
    renderSection();
    const reload = await screen.findByRole("region", {
      name: "Low balance alert",
    });
    expect(
      within(reload).queryByRole("checkbox", { name: "Auto-reload" }),
    ).not.toBeInTheDocument();
  });

  it("saves auto-reload, refusing an amount under the minimum top-up", async () => {
    api.summary = { ...SUMMARY, billing_available: true };
    api.put.mockResolvedValue({ data: { result: {} } });
    renderSection();
    const reload = await screen.findByRole("region", {
      name: "Low balance and auto-reload",
    });
    await userEvent.click(
      within(reload).getByRole("checkbox", { name: "Auto-reload" }),
    );
    await userEvent.type(
      within(reload).getByLabelText("Reload when balance falls below (USD)"),
      "5",
    );
    await userEvent.type(
      within(reload).getByLabelText("Reload amount (USD)"),
      "10",
    );
    await userEvent.click(within(reload).getByRole("button", { name: "Save" }));
    expect(
      within(reload).getByText("The reload amount is at least $25.00."),
    ).toBeInTheDocument();
    expect(api.put).not.toHaveBeenCalled();

    await userEvent.clear(within(reload).getByLabelText("Reload amount (USD)"));
    await userEvent.type(
      within(reload).getByLabelText("Reload amount (USD)"),
      "30",
    );
    await userEvent.click(within(reload).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        "/agentcc/managed-credits/settings/",
        {
          low_balance_threshold_usd: null,
          auto_reload: { enabled: true, threshold_usd: "5", amount_usd: "30" },
        },
      ),
    );
  });

  it("says plainly when the gateway's live balance cannot be read", async () => {
    api.summary = { ...SUMMARY, gateway: null, gateway_warning: "unreachable" };
    renderSection();
    expect(
      await screen.findByText(/live balance could not be read/),
    ).toBeInTheDocument();
  });

  it("renders no wallet for an organization that has not enabled managed models, in dark mode", async () => {
    api.summary = { ...SUMMARY, enabled: false };
    renderSection(undefined, "dark");
    await waitForSummary();
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
    expect(
      screen.queryByRole("link", { name: "Open the model catalog" }),
    ).toBeNull();
  });

  it("shows the server refusal text when checkout cannot be started", async () => {
    api.summary = { ...SUMMARY, billing_available: true };
    api.post.mockRejectedValue({
      result: "Credit purchase is unavailable in this deployment.",
    });
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Add credits" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Continue to payment" }),
    );
    expect(
      await screen.findByText(
        "Credit purchase is unavailable in this deployment.",
      ),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])(
    "does not expose the new wallet through history while credits are off (models available: %s)",
    async (managedModelsAvailable) => {
      api.summary = {
        ...SUMMARY,
        enabled: false,
        managed_models_available: managedModelsAvailable,
        billing_available: false,
      };
      renderSection();
      await waitForSummary();
      expect(
        screen.queryByRole("region", { name: "AI Gateway credits" }),
      ).toBeNull();
      expect(grid.props).toBeNull();
      expect(
        screen.queryByRole("region", { name: "Credit ledger" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Add credits" })).toBeNull();
      expect(api.post).not.toHaveBeenCalled();
    },
  );

  it("keeps estimated and reported spend for the same model as distinct rows", async () => {
    statementOverride.value = {
      ...STATEMENT,
      spend_by_model: [
        {
          model: "same/model",
          count: 2,
          spend_usd: "0.2",
          markup_usd: "0.01",
          estimated: false,
        },
        {
          model: "same/model",
          count: 3,
          spend_usd: "0.3",
          markup_usd: "0.02",
          estimated: true,
        },
      ],
    };
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    const reported = await within(statement).findByText("same/model", {
      exact: true,
    });
    const estimated = within(statement).getByText("same/model (estimated)", {
      exact: true,
    });
    expect(reported.closest("tr")).toHaveTextContent("-$0.20");
    expect(estimated.closest("tr")).toHaveTextContent("-$0.30");
    expect(reported.closest("tr")).not.toBe(estimated.closest("tr"));
  });

  it("does not claim an empty ledger when its entries read fails", async () => {
    api.entries = vi.fn(() => {
      throw new Error("read failed");
    });
    renderSection();
    expect(
      await screen.findByText(/ledger could not be loaded/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        /Grants, top-ups and Future AGI model spend appear here/,
      ),
    ).toBeNull();
  });

  it("does not replace a failed statement with zero balances", async () => {
    statementOverride.value = new Error("statement failed");
    renderSection();
    const statement = await screen.findByRole("region", {
      name: "Monthly statement",
    });
    expect(await within(statement).findByRole("alert")).toHaveTextContent(
      "The statement could not be loaded.",
    );
    expect(within(statement).queryByText("Closing balance")).toBeNull();
    expect(within(statement).queryByText("$0.00")).toBeNull();
  });

  it("does not offer a wallet while organization enablement is unresolved", async () => {
    api.summary = null;
    renderSection();
    await waitForSummary();
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
    expect(screen.queryByRole("region", { name: "Credit ledger" })).toBeNull();
  });

  it.each([401, 403, 503])(
    "renders no credit surface when the summary refuses with %i",
    async (statusCode) => {
      api.summary = Object.assign(new Error("unavailable"), { statusCode });
      renderSection();
      await waitForSummary("error");
      expect(
        screen.queryByRole("region", { name: "AI Gateway credits" }),
      ).toBeNull();
      expect(
        screen.queryByRole("region", { name: "Credit ledger" }),
      ).toBeNull();
      expect(screen.queryByText(/no entries/i)).toBeNull();
      expect(screen.queryByRole("button", { name: "Add credits" })).toBeNull();
    },
  );
});
