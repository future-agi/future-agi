import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// (react-apexcharts is stubbed globally in setupTests.js — jsdom can't lay it out.)

// Feed the summary a fixed run list with inline scores (a mock run), so
// useRunsSummary reads scores directly and makes no kpis fetch.
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn() };
});

const { useEnvironmentRuns } = await import("src/api/simulate-environments/runs");
const { default: RunsSummary } = await import("../RunsSummary");

const RUNS = [
  {
    id: "ex2", executionId: "ex2", ordinal: 2, label: "Run 2", status: "passed",
    startedAt: "2026-01-13T16:40:00.000Z", finishedAt: "2026-01-13T16:42:10.000Z",
    total: 20, passed: 15, failed: 5, agentVersion: "v2", durationS: 11.9,
    scores: { task_success: 58, policy_adherence: 39 },
  },
  {
    id: "ex1", executionId: "ex1", ordinal: 1, label: "Run 1", status: "failed",
    startedAt: "2026-01-12T11:05:00.000Z", finishedAt: "2026-01-12T11:07:30.000Z",
    total: 20, passed: 10, failed: 10, agentVersion: "v1", durationS: 13.2,
    scores: { task_success: 49, policy_adherence: 28 },
  },
];

const env = { id: "env-1", name: "Refund Support", version: "v3" };
const envState = { scenarios: Array.from({ length: 20 }, (_, i) => ({ id: `s${i}` })) };

function renderSummary(props = {}) {
  useEnvironmentRuns.mockReturnValue({ runs: RUNS, isLoading: false });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

describe("RunsSummary", () => {
  beforeEach(() => useEnvironmentRuns.mockReset());

  it("heads the summary with the run and scenario counts", () => {
    renderSummary();
    expect(screen.getByText("Simulations summary")).toBeInTheDocument();
    expect(screen.getByText("2 runs · 20 scenarios")).toBeInTheDocument();
  });

  it("shows a real pass rate per run", () => {
    renderSummary();
    expect(screen.getByText("75%")).toBeInTheDocument(); // Run 2: 15/20
    expect(screen.getByText("50%")).toBeInTheDocument(); // Run 1: 10/20
  });

  it("renders the derived eval columns from the runs' score keys", () => {
    renderSummary();
    // The eval name appears both in the graph legend and the table header.
    expect(screen.getAllByText("Task success").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Policy adherence").length).toBeGreaterThan(0);
    expect(screen.getByText("58%")).toBeInTheDocument(); // Run 2 task_success
    expect(screen.getByText("28%")).toBeInTheDocument(); // Run 1 policy_adherence
  });

  it("marks the un-backed columns as Dummy rather than inventing values", () => {
    renderSummary();
    // Tokens / Cost / Said not done / Mean return each carry a Dummy tag.
    expect(screen.getAllByText("Dummy").length).toBe(4);
  });

  it("defers Choose winner behind a disabled 'coming soon' control", () => {
    renderSummary();
    expect(screen.getByRole("button", { name: /Choose winner/ })).toBeDisabled();
  });

  it("renders a run with no ordinal without crashing the identity chip", () => {
    // A run whose ordinal never got stamped must not blow up the chip colour
    // (runColor(undefined) → alpha(undefined) would throw during style
    // serialization). Regression guard for the mock-runs render crash.
    useEnvironmentRuns.mockReturnValue({
      runs: [{ ...RUNS[0], ordinal: undefined }],
      isLoading: false,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    expect(() =>
      render(
        <QueryClientProvider client={client}>
          <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} />
        </QueryClientProvider>,
      ),
    ).not.toThrow();
  });

  it("opens a run when its row is clicked", () => {
    const onOpenRun = vi.fn();
    renderSummary({ onOpenRun });
    fireEvent.click(screen.getByText(/Run 2 · agent v2/));
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun.mock.calls[0][0]).toMatchObject({ executionId: "ex2" });
  });
});
