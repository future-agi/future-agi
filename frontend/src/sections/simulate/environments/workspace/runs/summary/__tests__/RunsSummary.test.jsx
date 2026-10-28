import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Feed the summary a fixed run list with inline scores (a mock run), so
// useRunsSummary reads scores directly and makes no kpis fetch. The chart's
// every-run list is mocked to the same runs unless a test says otherwise.
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useEnvironmentRuns: vi.fn() };
});
vi.mock("src/api/simulate-environments/useAllRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useAllRuns: vi.fn() };
});

const { useEnvironmentRuns } = await import(
  "src/api/simulate-environments/runs"
);
const { useAllRuns } = await import("src/api/simulate-environments/useAllRuns");
const { default: RunsSummary } = await import("../RunsSummary");
const { STATUS_META } = await import("../../runs.constants");
const { BUILD_TONES } = await import("../../../../buildEnvironment/buildTones");

const RUNS = [
  {
    id: "ex2",
    executionId: "ex2",
    ordinal: 2,
    label: "Run 2",
    status: "passed",
    startedAt: "2026-01-13T16:40:00.000Z",
    finishedAt: "2026-01-13T16:42:10.000Z",
    total: 20,
    passed: 15,
    failed: 5,
    agentVersion: "v2",
    durationS: 11.9,
    scores: { task_success: 58, policy_adherence: 39 },
  },
  {
    id: "ex1",
    executionId: "ex1",
    ordinal: 1,
    label: "Run 1",
    status: "failed",
    startedAt: "2026-01-12T11:05:00.000Z",
    finishedAt: "2026-01-12T11:07:30.000Z",
    total: 20,
    passed: 10,
    failed: 10,
    agentVersion: "v1",
    durationS: 13.2,
    scores: { task_success: 49, policy_adherence: 28 },
  },
];

const env = { id: "env-1", name: "Refund Support", version: "v3" };
const envState = {
  scenarios: Array.from({ length: 20 }, (_, i) => ({ id: `s${i}` })),
};

function renderSummary(
  props = {},
  runs = RUNS,
  isLoading = false,
  totals = {},
) {
  useEnvironmentRuns.mockReturnValue({
    runs,
    count: runs.length,
    isLoading,
    ...totals,
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <RunsSummary
        env={env}
        envState={envState}
        onStart={vi.fn()}
        onOpenRun={vi.fn()}
        onGo={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

// The nearest element holding both nodes — where a control actually sits.
function closestCommon(a, b) {
  let node = a.parentElement;
  while (node && !node.contains(b)) node = node.parentElement;
  return node;
}

describe("RunsSummary", () => {
  beforeEach(() => {
    useEnvironmentRuns.mockReset();
    useAllRuns.mockReset();
    // The chart reads the same runs the table's page holds.
    useAllRuns.mockImplementation((...args) => {
      const { runs = [] } = useEnvironmentRuns(...args) || {};
      return { runs, capped: false, isLoading: false };
    });
  });

  it("shows a spinner, not an empty summary, while the runs load", () => {
    renderSummary({}, [], true);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByText(/^Runs \(/)).toBeNull();
  });

  it("titles the runs table with the run and scenario counts, with no heading repeating the tab", () => {
    renderSummary();
    expect(screen.queryByText("Simulations summary")).toBeNull();
    expect(screen.getByText("Runs (2)")).toBeInTheDocument();
    expect(screen.getByText("across 20 scenarios")).toBeInTheDocument();
  });

  it("titles the runs table with the server's run-test totals, not the page's rows", () => {
    // A 2-row page of a 12-run history that covered 7 scenarios in all.
    renderSummary({}, RUNS, false, { count: 12, coveredScenarioCount: 7 });
    expect(screen.getByText("Runs (12)")).toBeInTheDocument();
    expect(screen.getByText("across 7 scenarios")).toBeInTheDocument();
  });

  it("puts the run actions on the runs table, leaving the chart card to its eval chips", () => {
    renderSummary();
    const graphControls = screen.getByRole("group", {
      name: "Pass rate across runs",
    });
    const tableTitle = screen.getByText("Runs (2)");
    for (const name of ["Add Evals", /Choose winner/]) {
      const area = closestCommon(
        tableTitle,
        screen.getByRole("button", { name }),
      );
      expect(area.contains(graphControls)).toBe(false);
    }
    const runsBar = closestCommon(
      tableTitle,
      screen.getByRole("button", { name: "Add Evals" }),
    );
    expect(
      within(runsBar).getByRole("button", { name: "Add Evals" }),
    ).toBeEnabled();
  });

  it("says scenario, not scenarios, for one", () => {
    renderSummary({}, RUNS, false, { count: 12, coveredScenarioCount: 1 });
    expect(screen.getByText("across 1 scenario")).toBeInTheDocument();
  });

  it("shows a real pass rate per run", () => {
    renderSummary();
    // Scoped to the table: the headline and chart print the same numbers.
    const table = within(screen.getByRole("table"));
    expect(table.getByText("75%")).toBeInTheDocument(); // Run 2: 15/20
    expect(table.getByText("50%")).toBeInTheDocument(); // Run 1: 10/20
  });

  it("renders the derived eval columns from the runs' score keys", () => {
    renderSummary();
    // The eval name appears both in the eval chips and the table header.
    expect(screen.getAllByText("Task success").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Policy adherence").length).toBeGreaterThan(0);
    const table = within(screen.getByRole("table"));
    expect(table.getByText("58%")).toBeInTheDocument(); // Run 2 task_success
    expect(table.getByText("28%")).toBeInTheDocument(); // Run 1 policy_adherence
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
    expect(
      screen.getByRole("button", { name: /Choose winner/ }),
    ).toBeDisabled();
  });

  it("renders a run with no ordinal without crashing the identity chip", () => {
    // A run whose ordinal never got stamped must not blow up the chip colour
    // (runColor(undefined) → alpha(undefined) would throw during style
    // serialization). Regression guard for the mock-runs render crash.
    useEnvironmentRuns.mockReturnValue({
      runs: [{ ...RUNS[0], ordinal: undefined }],
      isLoading: false,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    expect(() =>
      render(
        <QueryClientProvider client={client}>
          <RunsSummary
            env={env}
            envState={envState}
            onStart={vi.fn()}
            onOpenRun={vi.fn()}
            onGo={vi.fn()}
          />
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
        {
          ...RUNS[0],
          id: "ex3",
          executionId: "ex3",
          ordinal: 3,
          label: "Run 3",
          runState: "queued",
          stoppable: true,
        },
        { ...RUNS[0], runState: "finished", stoppable: false },
        { ...RUNS[1], runState: "running", stoppable: true },
      ],
      isLoading: false,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <RunsSummary
          env={env}
          envState={envState}
          onStart={vi.fn()}
          onOpenRun={vi.fn()}
          onGo={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(
      screen.getByRole("columnheader", { name: "Status" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    // Stop sits in the Status cell, only on the runs that can still be stopped.
    const stops = screen.getAllByRole("button", { name: "Stop simulation" });
    expect(stops).toHaveLength(2);
    expect(stops[0].closest("td")).toContainElement(screen.getByText("Queued"));
    expect(stops[1].closest("td")).toContainElement(
      screen.getByText("Running"),
    );
    expect(
      screen.getByText("Completed").closest("td").querySelector("button"),
    ).toBeNull();
    // A finished run reads green in the table, like the design.
    expect(STATUS_META.finished).toEqual({
      color: BUILD_TONES.green,
      label: "Completed",
    });
  });

  it("shows a stopped run as Cancelling, without a second Stop", () => {
    useEnvironmentRuns.mockReturnValue({
      runs: [{ ...RUNS[0], runState: "cancelling", stoppable: false }],
      isLoading: false,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <RunsSummary
          env={env}
          envState={envState}
          onStart={vi.fn()}
          onOpenRun={vi.fn()}
          onGo={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText("Cancelling")).toBeInTheDocument();
    expect(screen.queryByText("Running")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Stop simulation" }),
    ).toBeNull();
  });

  it("labels the latest finished run's point with its pass rate", () => {
    renderSummary();
    const chart = screen.getByRole("group", { name: "Pass rate across runs" });
    // Run 2 (15/20) is the latest, Run 1 (10/20) before it: two points.
    expect(chart.querySelectorAll("circle")).toHaveLength(2);
    expect(within(chart).getByText("75%")).toBeInTheDocument();
  });

  it("keeps a one-run environment's eval scores in view as chips", () => {
    renderSummary({}, [RUNS[1]]);
    const chart = screen.getByRole("group", { name: "Pass rate across runs" });
    expect(chart.querySelectorAll("circle")).toHaveLength(1);
    const chips = screen.getAllByRole("button", { pressed: false });
    expect(chips.map((c) => c.textContent)).toEqual([
      "Policy adherence28%",
      "Task success49%",
    ]);
  });

  it("shows the pager only once there is more than one page of runs", () => {
    renderSummary({}, RUNS, false, { count: 10 });
    expect(screen.queryByText(/Rows per page/)).toBeNull();
  });

  it("pages the runs table once the run-test holds more than ten runs", () => {
    renderSummary({}, RUNS, false, { count: 11 });
    expect(screen.getByText(/Rows per page/)).toBeInTheDocument();
  });

  it("draws the chart from every run, not the table's page", () => {
    // The table holds one page; the chart's list holds the whole history.
    const older = {
      ...RUNS[1],
      id: "ex0",
      executionId: "ex0",
      ordinal: 0,
      label: "Run 0",
    };
    useAllRuns.mockReturnValue({
      runs: [...RUNS, older],
      capped: false,
      isLoading: false,
    });
    renderSummary();
    const pagings = useEnvironmentRuns.mock.calls.map((call) => call[2]);
    expect(pagings.every((p) => p.pageSize === 10)).toBe(true);
    const chart = screen.getByRole("group", { name: "Pass rate across runs" });
    expect(chart.querySelectorAll("circle")).toHaveLength(3);
  });

  it("pins the runs table's header, since the table scrolls under a fixed graph", () => {
    renderSummary();
    const heads = document.querySelectorAll("thead th");
    expect(heads.length).toBeGreaterThan(0);
    heads.forEach((th) => expect(th).toHaveClass("MuiTableCell-stickyHeader"));
  });
});
