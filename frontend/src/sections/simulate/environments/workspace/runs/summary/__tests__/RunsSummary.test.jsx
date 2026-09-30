import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// jsdom can't lay the chart out; capture what it's handed instead.
const chartProps = vi.fn();
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chartProps(props);
    return null;
  },
}));
const lastChart = () => chartProps.mock.calls.at(-1)[0];

// Feed the summary a fixed run list with inline scores (a mock run), so
// useRunsSummary reads scores directly and makes no kpis fetch.
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn() };
});

const { useEnvironmentRuns } = await import("src/api/simulate-environments/runs");
const { default: RunsSummary } = await import("../RunsSummary");
const { STATUS_META } = await import("../../runs.constants");
const { BUILD_TONES } = await import("../../../../buildEnvironment/buildTones");

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

function renderSummary(props = {}, runs = RUNS, isLoading = false) {
  useEnvironmentRuns.mockReturnValue({ runs, isLoading });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

describe("RunsSummary", () => {
  beforeEach(() => useEnvironmentRuns.mockReset());

  it("shows a spinner, not an empty summary, while the runs load", () => {
    renderSummary({}, [], true);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText("Simulations summary")).toBeNull();
  });

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

  it("shows a plain dash for the un-backed columns rather than inventing values", () => {
    renderSummary();
    // Tokens / Cost / Said not done / Mean return have no backend field, so they
    // render a dashed cell with no "Dummy" tag.
    expect(screen.queryByText("Dummy")).toBeNull();
  });

  it("shows no run-compare checkboxes or compare hint in the runs table", () => {
    renderSummary();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByText(/compare them/)).toBeNull();
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

  it("labels the duration column as the run total, not an average", () => {
    renderSummary();
    // The column shows the run's total wall-clock (executionToRun.durationS), so
    // it must not claim to be an average.
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.queryByText("Avg duration")).toBeNull();
  });

  it("shows each run's own agent version, not the current environment version", () => {
    renderSummary();
    // Every run previously carried "× env <current>" — the same fixture version
    // for all of them, which misrepresents what each run actually ran against.
    expect(screen.getByText(/Run 2 · agent v2/)).toBeInTheDocument();
    expect(screen.queryByText(/× env/)).toBeNull();
  });

  it("opens a run when its row is clicked", () => {
    const onOpenRun = vi.fn();
    renderSummary({ onOpenRun });
    fireEvent.click(screen.getByText(/Run 2 · agent v2/));
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun.mock.calls[0][0]).toMatchObject({ executionId: "ex2" });
  });

  it("adds a Status column that reads each run's lifecycle", () => {
    useEnvironmentRuns.mockReturnValue({
      runs: [
        { ...RUNS[0], id: "ex3", executionId: "ex3", ordinal: 3, label: "Run 3", runState: "queued", stoppable: true },
        { ...RUNS[0], runState: "finished", stoppable: false },
        { ...RUNS[1], runState: "running", stoppable: true },
      ],
      isLoading: false,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    // Stop sits in the Status cell, only on the runs that can still be stopped.
    const stops = screen.getAllByRole("button", { name: "Stop simulation" });
    expect(stops).toHaveLength(2);
    expect(stops[0].closest("td")).toContainElement(screen.getByText("Queued"));
    expect(stops[1].closest("td")).toContainElement(screen.getByText("Running"));
    expect(screen.getByText("Completed").closest("td").querySelector("button")).toBeNull();
    // A finished run reads green in the table, like the design.
    expect(STATUS_META.finished).toEqual({ color: BUILD_TONES.green, label: "Completed" });
  });

  it("shows a stopped run as Cancelling, without a second Stop", () => {
    useEnvironmentRuns.mockReturnValue({
      runs: [{ ...RUNS[0], runState: "cancelling", stoppable: false }],
      isLoading: false,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(screen.getByText("Cancelling")).toBeInTheDocument();
    expect(screen.queryByText("Running")).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop simulation" })).toBeNull();
  });

  it("marks the newest run on the graph's axis", () => {
    renderSummary();
    expect(lastChart().options.xaxis.categories).toEqual(["Run 1", "Run 2 · latest"]);
  });

  it("draws a one-run environment as a column per eval, not a single stacked point", () => {
    renderSummary({}, [RUNS[1]]);
    expect(lastChart().type).toBe("bar");
    expect(lastChart().options.xaxis.categories).toEqual(["Run 1 · latest"]);
    expect(lastChart().series).toEqual([
      { name: "Task success", data: [49] },
      { name: "Policy adherence", data: [28] },
    ]);
  });

  describe("graph eval selection", () => {
    const SEVEN = ["e1", "e2", "e3", "e4", "e5", "e6", "e7"];
    const manyEvals = [
      {
        ...RUNS[1],
        scores: Object.fromEntries(SEVEN.map((k, i) => [k, 10 * (i + 1)])),
      },
    ];

    it("draws only the first five evals by default", () => {
      renderSummary({}, manyEvals);
      expect(lastChart().series.map((x) => x.name)).toEqual([
        "E1", "E2", "E3", "E4", "E5",
      ]);
      expect(screen.getByText("5 of 7 evals")).toBeInTheDocument();
    });

    it("draws every eval when there are five or fewer", () => {
      renderSummary();
      expect(lastChart().series).toHaveLength(2);
      expect(screen.getByText("All 2 evals")).toBeInTheDocument();
    });

    it("keeps the user's pick once they change it", () => {
      renderSummary({}, manyEvals);
      fireEvent.mouseDown(screen.getByRole("combobox"));
      fireEvent.click(screen.getByRole("option", { name: /E7/ }));
      expect(lastChart().series.map((x) => x.name)).toEqual([
        "E1", "E2", "E3", "E4", "E5", "E7",
      ]);
    });
  });

  it("pins the runs table's header, since the table scrolls under a fixed graph", () => {
    renderSummary();
    const heads = document.querySelectorAll("thead th");
    expect(heads.length).toBeGreaterThan(0);
    heads.forEach((th) => expect(th).toHaveClass("MuiTableCell-stickyHeader"));
  });
});
