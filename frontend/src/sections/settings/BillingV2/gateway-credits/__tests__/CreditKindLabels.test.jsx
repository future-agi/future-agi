import React, { forwardRef } from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import CreditLedger from "../CreditLedger";
import CreditStatement from "../CreditStatement";
import { formatAmount, formatAmountCell } from "../format";

const api = vi.hoisted(() => ({ entries: null, statement: null }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const gw = actual.endpoints.gateway;
  return {
    ...actual,
    default: {
      ...actual.default,
      get: async (url, config = {}) => {
        if (url === gw.managedCreditEntries) {
          return { data: { result: await api.entries(config.params) } };
        }
        if (url === gw.managedCreditStatement) {
          return { data: { result: await api.statement(config.params) } };
        }
        throw new Error(`unmocked GET ${url}`);
      },
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

// Every kind the ledger can write (ManagedCreditEntry.Kind; the entries API's
// ENTRY_KINDS in agentcc/serializers/managed_credits.py), with its sign under
// the managed_credit_entry_amount_sign constraint, and the words Billing
// shows for it.
const KINDS = [
  ["spend", -0.000664, "Spend"],
  ["usage_debit", -0.25, "Platform usage"],
  ["topup", 2, "Top-up"],
  ["topup_reversal", -2, "Top-up refund"],
  ["grant", 1, "Grant"],
  ["refund", 0.000664, "Refund"],
  ["adjustment", -0.5, "Adjustment"],
  ["expiry", -0.1, "Expiry"],
  ["dispute_hold", -2, "Dispute hold"],
  ["dispute_release", 2, "Dispute release"],
];
const entry = (kind, amount) => ({
  id: `e-${kind}`,
  occurred_at: "2026-11-11T07:26:35Z",
  kind,
  amount_usd: amount,
  request_id: "",
  model: "",
  provider: "",
});

function renderBilling(ui) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function ledgerWith(rows) {
  api.entries = vi.fn(() => ({ entries: rows, next_cursor: null }));
  renderBilling(<CreditLedger />);
  const ledger = await screen.findByRole("region", { name: "Credit ledger" });
  await waitFor(() => expect(grid.props?.rowData).toHaveLength(rows.length));
  return ledger;
}

const cellsOf = (col) =>
  within(screen.getByTestId("ag-grid"))
    .getAllByRole("gridcell")
    .filter((cell) => cell.getAttribute("data-col") === col);

const kindColumn = () =>
  grid.props.columnDefs.find((col) => col.field === "kind");

async function statementWith(creditsByKind) {
  api.statement = vi.fn(() => ({
    month: "2026-11",
    currency: "USD",
    opening_balance: 0,
    credits_by_kind: creditsByKind,
    spend_by_model: [],
    closing_balance: 1,
  }));
  renderBilling(<CreditStatement />);
  const statement = await screen.findByRole("region", {
    name: "Monthly statement",
  });
  await within(statement).findByText("Closing balance");
  return statement;
}

const lineAmount = (statement, label) =>
  within(within(statement).getByText(label).closest("tr")).getAllByRole(
    "cell",
  )[2].textContent;

describe("Credit ledger and statement kinds in words", () => {
  beforeEach(() => {
    grid.props = null;
    api.entries = null;
    api.statement = null;
  });

  // Walk B8: a refunded $2.00 top-up showed its raw key "topup_reversal" in
  // the ledger's Kind column. Every kind the ledger can write has a name.
  it("names every kind the ledger can write, a refunded top-up as Top-up refund", async () => {
    await ledgerWith(KINDS.map(([kind, amount]) => entry(kind, amount)));
    expect(cellsOf("kind").map((cell) => cell.textContent)).toEqual(
      KINDS.map(([, , label]) => label),
    );
    expect(screen.getByTestId("ag-grid")).not.toHaveTextContent("_");
    // The amount reads as before.
    const reversal = KINDS.findIndex(([kind]) => kind === "topup_reversal");
    expect(cellsOf("amount_usd")[reversal].textContent).toBe(
      formatAmountCell(-2),
    );
  });

  // A kind added on the server before this screen names it must still read
  // as words, not as a snake_case key.
  it("reads a kind it has no name for as words, never its snake_case key", async () => {
    await ledgerWith([entry("promo_clawback", -1)]);
    expect(cellsOf("kind").map((cell) => cell.textContent)).toEqual([
      "Promo clawback",
    ]);
  });

  // Money taken back is never coloured as healthy credit; the kinds that
  // already had a colour keep it.
  it("colours a refunded top-up and a dispute hold as money taken back, never as healthy credit", async () => {
    await ledgerWith([entry("topup_reversal", -2)]);
    const tone = (value) => kindColumn().cellRenderer({ value }).props.tone;
    expect(tone("topup_reversal")).toBe("warning");
    expect(tone("dispute_hold")).toBe("error");
    expect(tone("dispute_release")).toBe("pending");
    expect(tone("usage_debit")).toBe("neutral");
    expect(tone("promo_clawback")).toBe("neutral");
    expect(
      Object.fromEntries(
        ["spend", "topup", "grant", "refund", "adjustment", "expiry"].map(
          (kind) => [kind, tone(kind)],
        ),
      ),
    ).toEqual({
      spend: "neutral",
      topup: "healthy",
      grant: "healthy",
      refund: "pending",
      adjustment: "warning",
      expiry: "error",
    });
  });

  // The Kind column's minimum width holds every name (see
  // CreditLedger.layout.test.jsx); a column the user narrows still cuts a
  // longer name like "Dispute release", so hover shows it whole, as Model
  // and Request ID do.
  it("shows a kind's whole name on hover, where a narrow Kind column cuts it", async () => {
    await ledgerWith([entry("dispute_release", 2)]);
    expect(kindColumn().tooltipValueGetter({ value: "dispute_release" })).toBe(
      "Dispute release",
    );
    expect(kindColumn().tooltipValueGetter({ value: "promo_clawback" })).toBe(
      "Promo clawback",
    );
  });

  it("offers every kind a user can see in the Kind filter, and filters by a refunded top-up", async () => {
    const ledger = await ledgerWith([entry("topup_reversal", -2)]);
    await userEvent.click(
      within(ledger).getByRole("combobox", { name: /Kind/ }),
    );
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["All", ...KINDS.map(([, , label]) => label)]);
    await userEvent.click(
      screen.getByRole("option", { name: "Top-up refund" }),
    );
    await waitFor(() =>
      expect(api.entries).toHaveBeenLastCalledWith({
        limit: 50,
        kind: "topup_reversal",
      }),
    );
  });

  // Walk B8: the month's statement listed the refund as "topup_reversal".
  // Each statement credit line (CREDIT_KINDS) has a name; amounts unchanged.
  it("names a month's top-up refunds and dispute lines in words, with their amounts unchanged", async () => {
    const statement = await statementWith({
      topup: 2,
      grant: 1,
      refund: 0,
      adjustment: 0,
      expiry: 0,
      topup_reversal: -2,
      dispute_hold: -0.5,
      dispute_release: 0.5,
    });
    expect(lineAmount(statement, "Top-ups")).toBe(formatAmount(2));
    expect(lineAmount(statement, "Grants")).toBe(formatAmount(1));
    expect(lineAmount(statement, "Top-up refunds")).toBe(formatAmount(-2));
    expect(lineAmount(statement, "Dispute holds")).toBe(formatAmount(-0.5));
    expect(lineAmount(statement, "Dispute releases")).toBe(formatAmount(0.5));
    expect(statement).not.toHaveTextContent("_");
  });

  it("reads a statement line of a kind it has no name for as words", async () => {
    const statement = await statementWith({ grant: 1, promo_clawback: -0.25 });
    expect(lineAmount(statement, "Promo clawback")).toBe(formatAmount(-0.25));
    expect(statement).not.toHaveTextContent("promo_clawback");
  });
});
