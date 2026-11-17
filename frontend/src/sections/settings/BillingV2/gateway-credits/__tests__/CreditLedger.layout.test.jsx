import React, { forwardRef } from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { CELL_PADDING_PX } from "src/sections/gateway/logs/__tests__/gridFit";

import CreditLedger from "../CreditLedger";

// Every kind the ledger can write (ManagedCreditEntry.Kind, the entries
// API's ENTRY_KINDS).
const LEDGER_KINDS = [
  "spend",
  "usage_debit",
  "topup",
  "topup_reversal",
  "grant",
  "refund",
  "adjustment",
  "expiry",
  "dispute_hold",
  "dispute_release",
];
// Each kind's name in the Kind badge's font, Inter 500 12 px (StatusBadge).
// Not browser-measured: Inter's advance widths from the bundled font file
// (@fontsource/inter latin 500), no kerning, rounded up plus 1 px, the
// method gridFit.js uses. A name missing here needs Infinity, so a new kind
// fails until it is measured.
const KIND_LABEL_PX = {
  Spend: 38,
  "Platform usage": 88,
  "Top-up": 44,
  "Top-up refund": 85,
  Grant: 33,
  Refund: 43,
  Adjustment: 67,
  Expiry: 37,
  "Dispute hold": 74,
  "Dispute release": 91,
};
// The badge's own padding, 6 px each side (StatusBadge px 0.75). A neutral
// kind reads as plain text, without it, so this is the most a name needs.
const KIND_BADGE_PADDING_PX = 12;
// Amount is a right-aligned column, and AG Grid gives a right-aligned cell
// tabular figures (ag-grid-community: .ag-right-aligned-cell
// { font-variant-numeric: tabular-nums }), so each digit, ".", "," and "-"
// takes a digit's width. Not browser-measured: Inter 400 12 px advance
// widths with the font's tnum substitutions, from the bundled font file
// (@fontsource/inter latin 400), no kerning, rounded up plus 1 px (73.2 px).
// The ledger is exact to the micro, so any signed amount under $10
// ("-$9.999999") is as wide as this one. A string missing here needs
// Infinity, so a changed format fails until it is measured.
const AMOUNT_CELL_PX = { "-$0.000664": 75 };
// The managed provider's name in a plain cell (proportional figures), by
// the same method (60.5 px).
const PROVIDER_CELL_PX = { "Future AGI": 62 };

const api = vi.hoisted(() => ({ entries: null }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: async (url, config = {}) => {
        if (url === actual.endpoints.gateway.managedCreditEntries) {
          return { data: { result: await api.entries(config.params) } };
        }
        throw new Error(`unmocked GET ${url}`);
      },
    },
  };
});
const grid = vi.hoisted(() => ({ props: null }));
// The grid's own body, as AG Grid renders it: its layout class follows
// domLayout, and an auto-height body is where AG Grid's theme puts a 150 px
// minimum height.
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import(
    "src/sections/gateway/mcp/testing/agGridMock"
  );
  return {
    AgGridReact: forwardRef(function MockGrid(props, _ref) {
      grid.props = props;
      // eslint-disable-next-line react/prop-types
      const autoHeight = props.domLayout === "autoHeight";
      const layout = autoHeight ? "auto-height" : "normal";
      return (
        <div className={`ag-root ag-layout-${layout}`}>
          <div className="ag-center-cols-viewport" data-testid="viewport">
            <MockAgGrid {...props} />
          </div>
        </div>
      );
    }),
  };
});

const entry = (index) => ({
  id: `e${index}`,
  occurred_at: "2026-10-20T10:10:05Z",
  kind: "spend",
  amount_usd: -0.000664,
  request_id: `req_${index}`,
  model: "openai/gpt-4o",
  provider: "openrouter",
});
const entries = (count) =>
  Array.from({ length: count }, (_, index) => entry(index));

function renderLedger() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter>
          <CreditLedger />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function ledgerWithRows(count) {
  api.entries = vi.fn(() => ({ entries: entries(count), next_cursor: null }));
  renderLedger();
  await screen.findByRole("region", { name: "Credit ledger" });
  await waitFor(() => expect(grid.props?.rowData).toHaveLength(count));
  return grid.props;
}

const column = (props, key) =>
  props.columnDefs.find((col) => col.field === key || col.colId === key);

describe("Credit ledger layout", () => {
  beforeEach(() => {
    grid.props = null;
  });

  // A fixed 420-px grid left about 300 px of empty card
  // under a single entry. A short ledger now sizes to its rows, and drops
  // AG Grid's 150 px minimum body height, which alone left about 110 px of
  // empty grid body under one entry.
  it("sizes the grid to a single entry, with no empty card under it", async () => {
    const props = await ledgerWithRows(1);
    expect(props.domLayout).toBe("autoHeight");
    expect(screen.getByTestId("gateway-grid")).not.toHaveStyle({
      height: "420px",
    });
    expect(screen.getByTestId("viewport")).toHaveStyle({ minHeight: "0" });
  });

  // While the ledger loads there are no rows yet: AG Grid keeps its minimum
  // body height, so the loading overlay keeps its room.
  it("keeps AG Grid's minimum body height while the ledger loads", async () => {
    api.entries = vi.fn(() => new Promise(() => {}));
    renderLedger();
    await screen.findByRole("region", { name: "Credit ledger" });
    await waitFor(() => expect(grid.props?.loading).toBe(true));
    expect(grid.props.domLayout).toBe("autoHeight");
    expect(screen.getByTestId("viewport")).not.toHaveStyle({ minHeight: "0" });
  });

  it("still sizes the grid to its rows at nine entries, under the 420-px cap", async () => {
    const props = await ledgerWithRows(9);
    expect(props.domLayout).toBe("autoHeight");
  });

  // A full page of entries must not grow the Billing page by thousands of
  // pixels: past nine rows the ledger scrolls inside its 420-px grid.
  it("keeps a long ledger scrolling inside a fixed grid", async () => {
    const props = await ledgerWithRows(12);
    expect(props.domLayout ?? "normal").toBe("normal");
    expect(screen.getByTestId("gateway-grid")).toHaveStyle({
      height: "420px",
    });
  });

  // Picking a Kind loads that kind's entries. While they load, a long
  // ledger keeps its 420-px grid instead of shrinking to the loading overlay
  // and growing back, so the sections under it do not jump.
  it("keeps a long ledger's 420-px grid while a newly picked kind loads", async () => {
    api.entries = vi.fn((params) =>
      params.kind
        ? new Promise(() => {})
        : { entries: entries(12), next_cursor: null },
    );
    renderLedger();
    const ledger = await screen.findByRole("region", { name: "Credit ledger" });
    await waitFor(() => expect(grid.props?.rowData).toHaveLength(12));
    await userEvent.click(
      within(ledger).getByRole("combobox", { name: /Kind/ }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Grant" }));
    await waitFor(() =>
      expect(api.entries).toHaveBeenLastCalledWith({
        limit: 50,
        kind: "grant",
      }),
    );
    expect(grid.props.loading).toBe(true);
    expect(grid.props.rowData).toBeUndefined();
    expect(grid.props.domLayout ?? "normal").toBe("normal");
    expect(screen.getByTestId("gateway-grid")).toHaveStyle({
      height: "420px",
    });
  });

  // Reviewer minors: an exact spend like -$0.000664 was cut at 1024 px in a
  // 96-px Amount column, and Request ID had no hover text like Model.
  it("gives the widest signed amount room and Request ID a hover tooltip, still fitting a 1024-px card", async () => {
    const props = await ledgerWithRows(1);
    expect(column(props, "amount_usd").minWidth).toBeGreaterThanOrEqual(104);
    expect(column(props, "request_id").tooltipField).toBe("request_id");
    const total = props.columnDefs.reduce(
      (sum, col) => sum + (col.minWidth || 0),
      0,
    );
    // The 1024-px no-clip rule: room for a 15 px scrollbar in a ~700 px card.
    expect(total).toBeLessThanOrEqual(685);
  });

  // UX review P3 (ledger pair): at 1024 px the grid sits at its minimum
  // widths, and the 110-px Kind column cut "Top-up refund", "Platform usage",
  // "Dispute hold" and "Dispute release" inside their badge. Only a mouse
  // hover read them; the ledger's cells take no focus, so keyboard and touch
  // users could not. The Kind column now holds every kind's name whole.
  it("gives the Kind column room for its longest name at 1024 px, still fitting a 1024-px card", async () => {
    const props = await ledgerWithRows(1);
    const kind = column(props, "kind");
    const labels = LEDGER_KINDS.map(
      (value) => kind.cellRenderer({ value }).props.label,
    );
    const longest = Math.max(
      ...labels.map((label) => KIND_LABEL_PX[label] ?? Infinity),
    );
    expect(kind.minWidth).toBeGreaterThanOrEqual(
      longest + KIND_BADGE_PADDING_PX + CELL_PADDING_PX,
    );
    const total = props.columnDefs.reduce(
      (sum, col) => sum + (col.minWidth || 0),
      0,
    );
    expect(total).toBeLessThanOrEqual(685);
  });

  // At 1024 px Amount sits at its minimum width. At 104 px the spend
  // -$0.000664 showed cut short, so the amount could not be read. The room
  // comes from Provider, which still holds "Future AGI"; Time and Kind keep
  // theirs and the minimums still fit a 1024-px card.
  it("gives the Amount column room for a signed sub-cent spend in tabular figures at 1024 px, taken from Provider without cutting Future AGI", async () => {
    const props = await ledgerWithRows(1);
    const amount = column(props, "amount_usd");
    expect(amount.type).toBe("rightAligned");
    const spend = amount.valueFormatter({ value: -0.000664 });
    expect(spend).toBe("-$0.000664");
    expect(amount.minWidth).toBeGreaterThanOrEqual(
      (AMOUNT_CELL_PX[spend] ?? Infinity) + CELL_PADDING_PX,
    );
    const provider = column(props, "provider");
    const name = provider.valueGetter({ data: { provider: "openrouter" } });
    expect(name).toBe("Future AGI");
    expect(provider.minWidth).toBeGreaterThanOrEqual(
      (PROVIDER_CELL_PX[name] ?? Infinity) + CELL_PADDING_PX,
    );
    expect(column(props, "kind").minWidth).toBeGreaterThanOrEqual(136);
    expect(column(props, "occurred_at").minWidth).toBeGreaterThanOrEqual(150);
    const total = props.columnDefs.reduce(
      (sum, col) => sum + (col.minWidth || 0),
      0,
    );
    expect(total).toBeLessThanOrEqual(685);
  });
});
