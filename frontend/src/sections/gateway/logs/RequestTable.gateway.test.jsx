import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import RequestTable from "./RequestTable";
import { BUILTIN_COLUMNS } from "./columns/columnModel";
import { GATEWAY_COLUMNS } from "./columns/gatewayColumns";

vi.mock("ag-grid-react", () => import("./__tests__/fakeAgGrid"));

// One log entry v2 row and one row from before log entry v2 (no new columns).
const mockState = {
  data: {
    results: [
      {
        id: "log-v2",
        request_id: "req-v2",
        model: "auto",
        provider: "openrouter",
        status_code: 200,
        latency_ms: 840,
        cost: "0.001260",
        started_at: "2026-10-15T10:01:00Z",
        metadata: {},
        generation_id: "req-v2",
        billing_mode: "managed",
        credential_type: "managed",
        cost_upstream: "0.00120000",
        cost_markup: "0.00006000",
        attempt_count: 3,
        agent_id: "planner",
        agent_step: "act",
        // A run id with characters the URL must encode.
        agent_run_id: "run/7 a",
      },
      {
        id: "log-v1",
        request_id: "req-v1",
        model: "gpt-4o-mini",
        provider: "openai",
        status_code: 200,
        latency_ms: 300,
        cost: "0.000500",
        started_at: "2026-10-15T10:00:00Z",
        metadata: {},
        // As D1 lists a pre-v2 row: null v2 fields, and attempt_count at the
        // column's default of 1, which counts nothing.
        fallback_used: true,
        generation_id: null,
        billing_mode: null,
        credential_type: null,
        cost_upstream: null,
        cost_markup: null,
        attempt_count: 1,
        agent_id: null,
        agent_step: null,
        agent_run_id: null,
      },
    ],
    count: 2,
  },
  isLoading: false,
  error: null,
  refetch: vi.fn(),
};

// These tests cover run links: the Runs pages are routed. RunLink.test
// covers the plain-text run id a hidden Runs page (shell/page-status.js)
// would show instead.
vi.mock("../shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayPageHidden: () => false,
}));
vi.mock("./hooks/useRequestLogs", () => ({
  default: () => mockState,
}));

const columns = [BUILTIN_COLUMNS[0], ...GATEWAY_COLUMNS];

const rows = () => within(screen.getByTestId("ag-grid")).getAllByRole("row");

describe("RequestTable Agent & billing columns", () => {
  it("renders the eight columns' headers and values", () => {
    render(
      <RequestTable
        filters={{}}
        setFilter={vi.fn()}
        setFilters={vi.fn()}
        onSelectLog={vi.fn()}
        columns={columns}
      />,
    );
    expect(
      within(rows()[0])
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual([
      "Timestamp",
      "Billing mode",
      "Credential type",
      "Upstream cost",
      "Markup",
      "Attempts",
      "Agent",
      "Step",
      "Run",
    ]);
    const cells = within(rows()[1]).getAllByRole("cell");
    expect(cells.slice(1).map((td) => td.textContent)).toEqual([
      "Future AGI credits",
      "Future AGI",
      "$0.0012",
      "$0.00006",
      "3",
      "planner",
      "act",
      "run/7 a",
    ]);
  });

  it("links the run to its run page", () => {
    render(
      <RequestTable
        filters={{}}
        setFilter={vi.fn()}
        setFilters={vi.fn()}
        onSelectLog={vi.fn()}
        columns={columns}
      />,
    );
    expect(screen.getByRole("link", { name: "run/7 a" })).toHaveAttribute(
      "href",
      "/dashboard/gateway/runs/run%2F7%20a",
    );
  });

  it("follows the run link without opening the request drawer", async () => {
    const user = userEvent.setup();
    const onSelectLog = vi.fn();
    render(
      <RequestTable
        filters={{}}
        setFilter={vi.fn()}
        setFilters={vi.fn()}
        onSelectLog={onSelectLog}
        columns={columns}
      />,
    );
    await user.click(screen.getByRole("link", { name: "run/7 a" }));
    expect(onSelectLog).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe(
      "/dashboard/gateway/runs/run%2F7%20a",
    );

    await user.click(within(rows()[2]).getAllByRole("cell")[1]);
    expect(onSelectLog).toHaveBeenCalledWith("log-v1");
  });

  it("shows a dash in every new column for a row from before log entry v2", () => {
    render(
      <RequestTable
        filters={{}}
        setFilter={vi.fn()}
        setFilters={vi.fn()}
        onSelectLog={vi.fn()}
        columns={columns}
      />,
    );
    const cells = within(rows()[2]).getAllByRole("cell");
    expect(cells.slice(1).map((td) => td.textContent)).toEqual(
      Array(8).fill("—"),
    );
    expect(within(rows()[2]).queryByRole("link")).toBeNull();
  });
});
