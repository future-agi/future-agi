import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import ManagedModelsCard from "src/sections/gateway/models/ManagedModelsCard";
import RequestTable from "src/sections/gateway/logs/RequestTable";
import { BUILTIN_COLUMNS } from "src/sections/gateway/logs/columns/columnModel";
import { GATEWAY_BY_ID } from "src/sections/gateway/logs/columns/gatewayColumns";
import { mountedGrids } from "src/sections/gateway/logs/__tests__/fakeAgGrid";
import {
  CREDITS,
  DAY_ROWS,
  OWN_KEY,
  microsOf,
  sumMicros,
} from "src/sections/gateway/logs/__tests__/priceFixtures";

import GatewayCreditsSection from "../GatewayCreditsSection";

// Price reconciliation, the credits card (central Billing > AI Gateway
// credits): Spent is the sum of what the ledger debited, the charged amounts
// of credits requests; own-key requests debit nothing. No figure, caption,
// column or statement line shows the margin inside a charge. The last case
// sums a day of Logs rows and checks it against Spent for the same requests.

const api = vi.hoisted(() => ({
  summary: null,
  entries: [],
  statement: null,
  get: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  api.get.mockImplementation(async (url) => {
    if (url === gw.managedCredits) return { data: { result: api.summary } };
    if (url === gw.managedCreditEntries)
      return { data: { result: { entries: api.entries, next_cursor: null } } };
    if (url === gw.managedCreditStatement)
      return { data: { result: api.statement } };
    throw new Error(`unmocked GET ${url}`);
  });
  return { ...actual, default: { ...actual.default, get: api.get } };
});
vi.mock("ag-grid-react", () =>
  import("src/sections/gateway/logs/__tests__/fakeAgGrid"),
);
vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));
const logs = vi.hoisted(() => ({ rows: [] }));
vi.mock("src/sections/gateway/logs/hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: logs.rows, count: logs.rows.length },
    isLoading: false,
    error: null,
    refetch: () => {},
  }),
}));

const isCredits = (row) => row.billing_mode === "managed";
const chargedOf = (row) => Math.round(Number(row.cost) * 1_000_000);
const upstreamOf = (row) => Math.round(Number(row.cost_upstream) * 1_000_000);

// What the ledger reports for these requests: each credits request debited
// its charged amount, with the margin the serializer still returns.
function ledgerFor(rows) {
  const spend = rows.filter(isCredits);
  const spentMicros = sumMicros(spend.map(chargedOf));
  const marginMicros = sumMicros(spend.map((r) => chargedOf(r) - upstreamOf(r)));
  return {
    summary: {
      enabled: true,
      granted_usd: 25,
      settled_spend_usd: spentMicros / 1_000_000,
      available_usd: (25_000_000 - spentMicros) / 1_000_000,
      gateway: null,
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
    },
    entries: spend.map((r) => ({
      id: `entry-${r.id}`,
      occurred_at: r.started_at,
      kind: "spend",
      amount_usd: -chargedOf(r) / 1_000_000,
      request_id: r.request_id,
      model: r.model,
      provider: "openrouter",
      markup_usd: (chargedOf(r) - upstreamOf(r)) / 1_000_000,
      cost_source: "catalog",
      stripe_ref: "",
    })),
    statement: {
      month: "2026-11",
      currency: "USD",
      opening_balance: 0,
      credits_by_kind: { topup: 0, grant: 25, refund: 0, adjustment: 0, expiry: 0 },
      spend_by_model: [
        {
          model: "openai/gpt-5",
          count: spend.length,
          spend_usd: spentMicros / 1_000_000,
          markup_usd: marginMicros / 1_000_000,
        },
      ],
      closing_balance: (25_000_000 - spentMicros) / 1_000_000,
    },
    spentMicros,
    marginMicros,
  };
}

const theme = createTheme({ palette: palette("light"), typography });
function renderWith(ui) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ThemeProvider theme={theme}>
        <MemoryRouter>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function creditsCard() {
  const section = await screen.findByRole("region", {
    name: "AI Gateway credits",
  });
  const spent = (await within(section).findByText("Spent")).nextSibling;
  const statement = await screen.findByRole("region", {
    name: "Monthly statement",
  });
  await within(statement).findByText("openai/gpt-5");
  const ledger = screen.getByRole("region", { name: "Credit ledger" });
  return { section, spent, statement, ledger };
}

function useLedger(rows) {
  const ledger = ledgerFor(rows);
  api.summary = ledger.summary;
  api.entries = ledger.entries;
  api.statement = ledger.statement;
  return ledger;
}

describe("credits card price reconciliation", () => {
  beforeEach(() => {
    logs.rows = [];
  });

  it("spends a credits request's charged amount and nothing for an own-key request", async () => {
    const credits = DAY_ROWS[0];
    const own = DAY_ROWS[1];
    expect(chargedOf(credits)).toBe(CREDITS.chargedMicros);
    expect(chargedOf(own)).toBe(OWN_KEY.chargedMicros);
    useLedger([credits, own]);
    renderWith(<GatewayCreditsSection />);
    const { section, spent, statement } = await creditsCard();
    expect(spent).toHaveTextContent(CREDITS.charged);
    expect(spent.textContent).toBe(CREDITS.charged);
    const line = within(statement).getByText("openai/gpt-5").closest("tr");
    expect(line).toHaveTextContent(`-${CREDITS.charged}`);
    // No margin: not as a caption, a figure, a column or a statement cell.
    expect(section).not.toHaveTextContent(/markup|provider cost|%/i);
    expect(
      within(statement).queryByRole("columnheader", { name: "Markup" }),
    ).toBeNull();
    expect(section).not.toHaveTextContent(CREDITS.margin);
  });

  it("lists each credits charge in the ledger once, with no margin column", async () => {
    useLedger([DAY_ROWS[0], DAY_ROWS[1]]);
    renderWith(<GatewayCreditsSection />);
    const { ledger } = await creditsCard();
    await waitFor(() =>
      expect(within(ledger).getAllByRole("row").length).toBeGreaterThan(1),
    );
    const [head, ...rows] = within(ledger).getAllByRole("row");
    const headers = within(head)
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(headers).not.toContain("Markup");
    // Not even as a column the grid's menu can show.
    const defs = mountedGrids().find((props) =>
      props.columnDefs?.some((col) => col.field === "amount_usd"),
    ).columnDefs;
    expect(defs.map((col) => col.field || col.colId)).not.toContain(
      "markup_usd",
    );
    expect(defs.map((col) => col.headerName)).not.toContain("Markup");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent(`-${CREDITS.charged}`);
    expect(rows[0]).not.toHaveTextContent(CREDITS.margin);
  });

  it("shows no markup on the Models page credits card", () => {
    const { container } = renderWith(
      <ManagedModelsCard
        status={{
          available: true,
          enabled: true,
          balance_usd: 24.957479,
          markup_percent: 5,
        }}
        gatewayId="gw-1"
        canWrite
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Credit balance").nextSibling).toHaveTextContent(
      "$24.957479",
    );
    expect(container).not.toHaveTextContent(/markup|future agi price|%/i);
  });

  it("sums a day of Logs costs on credits to the card's Spent", async () => {
    const ledger = useLedger(DAY_ROWS);
    logs.rows = DAY_ROWS;
    renderWith(
      <>
        <RequestTable
          filters={{}}
          setFilter={() => {}}
          setFilters={() => {}}
          onSelectLog={() => {}}
          columns={[...BUILTIN_COLUMNS, GATEWAY_BY_ID.get("gateway:billingMode")]}
        />
        <GatewayCreditsSection />
      </>,
    );
    const { spent } = await creditsCard();
    const table = screen.getAllByTestId("ag-grid")[0];
    const [head, ...rows] = within(table).getAllByRole("row");
    const headers = within(head)
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    const cost = headers.indexOf("Cost");
    const mode = headers.indexOf("Billing mode");
    const cells = rows.map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((td) => td.textContent),
    );
    expect(cells).toHaveLength(DAY_ROWS.length);
    const creditsCells = cells.filter((c) => c[mode] === "Future AGI credits");
    expect(creditsCells).toHaveLength(DAY_ROWS.filter(isCredits).length);
    const loggedMicros = sumMicros(creditsCells.map((c) => microsOf(c[cost])));

    expect(microsOf(spent.textContent)).toBe(loggedMicros);
    expect(loggedMicros).toBe(ledger.spentMicros);
    // The sum is of charged amounts: the upstream costs would fall short by
    // exactly the margin, and own-key costs are not in it.
    const upstreamMicros = sumMicros(DAY_ROWS.filter(isCredits).map(upstreamOf));
    expect(loggedMicros - upstreamMicros).toBe(ledger.marginMicros);
    expect(ledger.marginMicros).toBeGreaterThan(0);
    const allMicros = sumMicros(cells.map((c) => microsOf(c[cost])));
    expect(allMicros).toBeGreaterThan(loggedMicros);
  });
});
