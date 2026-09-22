import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The wrapper owns the network hook; feed it a fixed set of mapped tasks.
const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunCalls: (...args) => useRunCalls(...args),
}));

const { default: RunTraceTable } = await import("../RunTraceTable");

const TASKS = [
  { id: "t1", scenario: "Refund a double charge", persona: "Impatient caller", status: "passed", critical: false, csat: 8, turns: 5, latencyMs: 300, tokens: null, durationMs: 40000, evalResults: [{ id: "eval-1", name: "Tone", score: 0.9, passed: true }] },
  { id: "t2", scenario: "Escalate to a human", persona: "Angry caller", status: "failed", critical: false, csat: 3, turns: 12, latencyMs: 600, tokens: null, durationMs: 80000, evalResults: [{ id: "eval-1", name: "Tone", score: 0.3, passed: false }] },
  { id: "t3", scenario: "Handle a timeout", persona: "Caller", status: "error", critical: false, csat: null, turns: null, latencyMs: null, tokens: null, durationMs: null, evalResults: [] },
];
const COLUMNS = [{ key: "eval-1", label: "Tone", group: "Evaluations" }];

const renderTable = (props = {}) =>
  render(<RunTraceTable executionId="ex1" onOpenCall={vi.fn()} {...props} />);

describe("RunTraceTable", () => {
  beforeEach(() => {
    useRunCalls.mockReturnValue({ tasks: TASKS, columns: COLUMNS, count: TASKS.length, isLoading: false });
  });

  it("renders the real calls, grouped by scenario, with the eval column", async () => {
    const user = userEvent.setup();
    renderTable();

    // Groups (one per scenario) render; the eval column header is present.
    expect(screen.getByText("Refund a double charge")).toBeInTheDocument();
    expect(screen.getByText("Tone")).toBeInTheDocument();

    // Groups start collapsed — expand to reveal the rows, then the persona cell.
    await user.click(screen.getByRole("button", { name: /Expand all/ }));
    expect(screen.getByText("Impatient caller")).toBeInTheDocument();
  });

  it("fires onOpenCall with the task on a row click", async () => {
    const user = userEvent.setup();
    const onOpenCall = vi.fn();
    renderTable({ onOpenCall });

    await user.click(screen.getByRole("button", { name: /Expand all/ }));
    await user.click(screen.getByText("Impatient caller"));

    expect(onOpenCall).toHaveBeenCalledWith(expect.objectContaining({ id: "t1" }));
  });

  it("narrows the rows when a status chip is clicked", async () => {
    const user = userEvent.setup();
    renderTable();

    // All three scenarios show at first.
    expect(screen.getByText("Refund a double charge")).toBeInTheDocument();
    // The Failing chip carries a count of 2 (the failed + errored calls).
    await user.click(screen.getByRole("button", { name: "Failing" }));

    expect(screen.queryByText("Refund a double charge")).toBeNull();
    expect(screen.getByText("Escalate to a human")).toBeInTheDocument();
    expect(screen.getByText("Handle a timeout")).toBeInTheDocument();
  });

  it("re-buckets the rows when the group-by axis changes to Status", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.click(screen.getByRole("button", { name: /Group by/ }));
    await user.click(screen.getByRole("menuitem", { name: "Status" }));

    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Errored")).toBeInTheDocument();
  });

  it("surfaces the failed-critical count through the seam", () => {
    const onFailedCriticalChange = vi.fn();
    renderTable({ onFailedCriticalChange });
    // No per-call critical field yet → the seam reports 0.
    expect(onFailedCriticalChange).toHaveBeenCalledWith(0);
  });

  it("shows an empty state instead of a table when there are no calls", () => {
    useRunCalls.mockReturnValue({ tasks: [], columns: [], count: 0, isLoading: false });
    renderTable();
    expect(screen.getByText(/No calls match that filter/)).toBeInTheDocument();
  });
});
