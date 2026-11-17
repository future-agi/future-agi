import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import RequestTable from "../RequestTable";
import RequestDetailDrawer from "../RequestDetailDrawer";

// The title "—" carries on a request no charge was computed for (logCost.js).
const NO_CHARGE = "No charge computed";

// One rule for a request's cost in the row and the drawer: "$0.00" only when
// a charge was computed and came to zero (the gateway priced the request and
// reports a cost source: a cache hit, a free model); "—", titled "No charge
// computed", when none was (refused, or failed before anything was served:
// a cost of 0 and no cost source). Before, a refused row read "$0.00" while
// a served row without a price read "—". An amount other than zero always
// reads as it is.

vi.mock("ag-grid-react", () => import("./fakeAgGrid"));

const { state } = vi.hoisted(() => ({
  state: { rows: [], detail: {} },
}));

vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: state.rows, count: state.rows.length },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../hooks/useRequestDetail", () => ({
  default: () => ({
    data: { result: state.detail },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("../../guardrails/FeedbackWidget", () => ({
  default: () => <div>Feedback widget</div>,
}));

const BASE = {
  model: "gpt-4o-mini",
  provider: "openai",
  latency_ms: 12,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  session_id: "",
  started_at: "2026-11-11T10:02:00Z",
  metadata: null,
};

// Refused before it was priced: a cost of 0, no cost source; the route had
// already set its billing mode.
const REFUSED = {
  ...BASE,
  id: "refused",
  request_id: "req-refused",
  status_code: 403,
  is_error: true,
  error_message: "Forbidden",
  cost: "0.000000",
  cost_source: null,
  billing_mode: "managed",
};

// Priced by the gateway at zero: a cache hit.
const CACHE_HIT = {
  ...BASE,
  id: "cache-hit",
  request_id: "req-cache-hit",
  status_code: 200,
  cache_hit: true,
  cost: "0.000000",
  cost_source: "cache_hit",
  billing_mode: "byok",
};

// An amount with no cost source (an older log Django priced) keeps it.
const LEGACY_PRICED = {
  ...BASE,
  id: "legacy",
  request_id: "req-legacy",
  status_code: 200,
  cost: "0.001000",
  cost_source: null,
  billing_mode: null,
};

const baseProps = {
  filters: {},
  setFilter: vi.fn(),
  setFilters: vi.fn(),
  onSelectLog: vi.fn(),
  onClearFilters: vi.fn(),
};

const gridRows = () =>
  within(screen.getByTestId("ag-grid")).getAllByRole("row").slice(1);

const costCell = (row) => {
  const headers = within(screen.getByTestId("ag-grid"))
    .getAllByRole("columnheader")
    .map((th) => th.textContent);
  return row.querySelectorAll("td")[headers.indexOf("Cost")];
};

const summaryCost = () =>
  screen
    .getAllByText("Cost")
    .map((el) => el.closest("li"))
    .find(Boolean);

const billingRow = (label) => screen.getByText(label).parentElement;

describe("a request's cost: no charge computed reads an em dash, a computed zero $0.00", () => {
  it("the Logs row reads — titled No charge computed for a refused request, $0.00 for a cache hit", () => {
    state.rows = [REFUSED, CACHE_HIT, LEGACY_PRICED];
    render(<RequestTable {...baseProps} />);
    const [refused, cacheHit, legacy] = gridRows();
    expect(costCell(refused)).toHaveTextContent(/^—$/);
    expect(within(costCell(refused)).getByTitle(NO_CHARGE)).toBeVisible();
    expect(costCell(refused)).not.toHaveTextContent("$0.00");
    expect(costCell(cacheHit)).toHaveTextContent("$0.00");
    expect(costCell(legacy)).toHaveTextContent("$0.001");
  });

  it("the drawer's Cost and Billed read — with No charge computed for a refused request", () => {
    state.detail = {
      ...REFUSED,
      billing: {
        mode: "managed",
        credential_type: "managed",
        billed: "0.000000",
        cost_source: null,
      },
      metadata: { billing_mode: "managed" },
    };
    render(<RequestDetailDrawer open logId="refused" onClose={vi.fn()} />);
    expect(summaryCost()).toHaveTextContent("—");
    expect(summaryCost()).toHaveTextContent(NO_CHARGE);
    expect(summaryCost()).not.toHaveTextContent("$0.00");
    expect(billingRow("Billed")).toHaveTextContent(/^Billed—$/);
  });

  it("the drawer's Cost reads $0.00 for a request priced at zero", () => {
    state.detail = {
      ...CACHE_HIT,
      billing: {
        mode: "byok",
        credential_type: "byok",
        billed: "0.000000",
        cost_source: "cache_hit",
      },
      metadata: { billing_mode: "byok" },
    };
    render(<RequestDetailDrawer open logId="cache-hit" onClose={vi.fn()} />);
    expect(summaryCost()).toHaveTextContent("$0.00");
    expect(summaryCost()).not.toHaveTextContent(NO_CHARGE);
  });
});
