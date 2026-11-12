import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";
import { batchResultsExport } from "./batchResultsExport";

const state = vi.hoisted(() => ({ batch: null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useBatchJobs", () => ({
  BATCH_TERMINAL_STATUSES: new Set(["completed", "failed", "cancelled"]),
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: state.batch, isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/gateway/settings?tab=batch-jobs&batch_id=batch-pricing");
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

function showBatch(results, totalCost, unpriced) {
  state.batch = {
    batch_id: "batch-pricing",
    status: "completed",
    total: results.length,
    summary: {
      total_cost: totalCost,
      unpriced_requests: unpriced,
      cost_known: unpriced === 0,
      completed: results.length,
      failed: 0,
      cancelled: 0,
    },
    results: results.map((result, index) => ({ index, status: "success", ...result })),
  };
  render(<BatchJobsTab gatewayId="gateway-pricing" config={{ batch: {} }} />);
  const dialog = screen.getByRole("dialog", { name: /Batch Details/ });
  return {
    total: within(dialog).getByText("Total Cost").parentElement,
    table: within(dialog).getByRole("table"),
  };
}

describe("finalized charge display", () => {
  it.each([
    ["unknown success", { cost: null, cost_known: false }],
    ["usage bearing error", { status: "error", cost: null, cost_known: false }],
    ["partial zero", { cost: 0, cost_known: false }],
    ["partial positive", { cost: 0.125, cost_known: false }],
    ["missing evidence", { cost: 0.125 }],
    ["missing amount", { cost_known: true }],
    ["unsettled cancellation", { status: "cancelled", cost: null, cost_known: false }],
  ])("shows no number for %s", async (_name, item) => {
    const { total, table } = await showBatch([item], null, 1);
    expect(total).toHaveTextContent("Cost not available");
    expect(total).not.toHaveTextContent("$");
    expect(table).toHaveTextContent("Not available");
    expect(table).not.toHaveTextContent("$");
  });

  it("does not display a lower bound for a mixed batch", async () => {
    const { total, table } = await showBatch([
      { cost: 0.125, cost_known: true },
      { status: "error", cost: null, cost_known: false },
    ], null, 1);
    expect(total).toHaveTextContent("Cost not available");
    expect(total).not.toHaveTextContent("$");
    expect(table).toHaveTextContent("$0.1250");
    expect(table).toHaveTextContent("Not available");
  });

  it.each(["success", "error", "cancelled"])("preserves known zero %s", async (status) => {
    const { total, table } = await showBatch([{ status, cost: 0, cost_known: true }], 0, 0);
    expect(total).toHaveTextContent("$0.0000");
    expect(table).toHaveTextContent("$0.0000");
  });

  it("preserves the customer's priced failed charge", async () => {
    const { total, table } = await showBatch([{ status: "error", cost: 0.125, cost_known: true }], 0.125, 0);
    expect(total).toHaveTextContent("$0.1250");
    expect(table).toHaveTextContent("$0.1250");
    expect(table).not.toHaveTextContent("Not available");
  });
  it("shows the reason and no number for a truncated provider answer", async () => {
    const reason = 'provider_200: Provider "openai" failed: reading provider response: unexpected EOF';
    const { total, table } = await showBatch([{ status: "error", error: reason, cost: null, cost_known: false, billable: false }], null, 1);
    expect(total).toHaveTextContent("Cost not available");
    expect(total).not.toHaveTextContent("$");
    expect(table).toHaveTextContent("Not available");
    expect(table).toHaveTextContent("reading provider response: unexpected EOF");
    expect(table).not.toHaveTextContent("$");
  });

  it("totals a priced failure whose fallback measured zero tokens", async () => {
    const { total, table } = await showBatch([{ cost: 0.125, cost_known: true, billable: true, input_tokens: 0, output_tokens: 0 }], 0.125, 0);
    expect(total).toHaveTextContent("$0.1250");
    expect(table).toHaveTextContent("$0.1250");
    expect(table).not.toHaveTextContent("Not available");
  });

  it.each(["json", "copy", "jsonl"])("exports a measured-zero fallback's known total in %s", async (format) => {
    await showBatch([{ cost: 0.125, cost_known: true, billable: true }], 0.125, 0);
    const text = batchResultsExport(state.batch, "batch-pricing", format);
    const records = format === "jsonl" ? text.trim().split("\n").map(line => JSON.parse(line)) : [JSON.parse(text)];
    expect(records[0].summary).toMatchObject({ total_cost: 0.125, cost_known: true });
    expect(format === "jsonl" ? records[1] : records[0].results[0]).toMatchObject({ cost: 0.125, cost_known: true });
  });

  it.each(["json", "copy", "jsonl"])("preserves null amounts in %s exports", async (format) => {
    await showBatch([{ status: "error", cost: null, cost_known: false }], null, 1);
    const text = batchResultsExport(state.batch, "batch-pricing", format);
    const records = format === "jsonl" ? text.trim().split("\n").map(line => JSON.parse(line)) : [JSON.parse(text)];
    expect(records[0].summary).toMatchObject({ total_cost: null, cost_known: false });
    expect(format === "jsonl" ? records[1] : records[0].results[0]).toMatchObject({ cost: null, cost_known: false });
  });
});
