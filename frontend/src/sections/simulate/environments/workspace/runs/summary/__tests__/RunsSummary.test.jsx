import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
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

const useCompareRuns = vi.fn();
vi.mock("src/api/simulate-environments/compareRuns", () => ({
  useCompareRuns: (...args) => useCompareRuns(...args),
}));

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

function renderSummary(props = {}, runs = RUNS, isLoading = false, totals = {}) {
  useEnvironmentRuns.mockReturnValue({
    runs,
    count: runs.length,
    isLoading,
    ...totals,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = () => (
    <QueryClientProvider client={client}>
      <RunsSummary env={env} envState={envState} onStart={vi.fn()} onOpenRun={vi.fn()} onGo={vi.fn()} {...props} />
    </QueryClientProvider>
  );
  const view = render(ui());
  return { rerender: () => view.rerender(ui()) };
}

// The nearest element holding both nodes — where a control actually sits.
function closestCommon(a, b) {
  let node = a.parentElement;
  while (node && !node.contains(b)) node = node.parentElement;
  return node;
}

describe("RunsSummary", () => {
  beforeEach(() => useEnvironmentRuns.mockReset());

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

  it("puts the run actions on the runs table, leaving the graph row to its legend", () => {
    renderSummary();
    const graphControls = screen.getByLabelText("Runs in graph");
    const tableTitle = screen.getByText("Runs (2)");
    for (const name of ["Add Evals", /Choose winner/]) {
      const area = closestCommon(tableTitle, screen.getByRole("button", { name }));
      expect(area.contains(graphControls)).toBe(false);
    }
    const runsBar = closestCommon(tableTitle, screen.getByRole("button", { name: "Add Evals" }));
    expect(within(runsBar).getByRole("button", { name: "Add Evals" })).toBeEnabled();
  });

  it("says scenario, not scenarios, for one", () => {
    renderSummary({}, RUNS, false, { count: 12, coveredScenarioCount: 1 });
    expect(screen.getByText("across 1 scenario")).toBeInTheDocument();
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

  it("shows no run-compare checkboxes or compare hint with versioning off", () => {
    renderSummary();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByText(/to compare/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
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

  it("tags a run whose agent differs from its environment version's", () => {
    renderSummary({}, [
      {
        ...RUNS[0],
        pinnedAgentVersion: { id: "a4", label: "v4" },
        environmentVersion: { id: "e2", label: "v2" },
        agentDiffers: true,
      },
      RUNS[1],
    ]);
    expect(screen.getAllByText("A4 differs from E2")).toHaveLength(1);
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

  it("marks the newest run on the graph's axis", async () => {
    renderSummary();
    fireEvent.mouseDown(screen.getByText("Latest 1 run"));
    fireEvent.click(await screen.findByRole("option", { name: "Latest 2 runs" }));
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
      fireEvent.mouseDown(screen.getByText("5 of 7 evals"));
      fireEvent.click(screen.getByRole("option", { name: /E7/ }));
      expect(lastChart().series.map((x) => x.name)).toEqual([
        "E1", "E2", "E3", "E4", "E5", "E7",
      ]);
    });
  });

  it("shows the pager only once there is more than one page of runs", () => {
    renderSummary({}, RUNS, false, { count: 10 });
    expect(screen.queryByText(/Rows per page/)).toBeNull();
  });

  it("pages the runs table once the run-test holds more than ten runs", () => {
    renderSummary({}, RUNS, false, { count: 11 });
    expect(screen.getByText(/Rows per page/)).toBeInTheDocument();
  });

  it("draws the graph from the head of the table's first page, not a query of its own", () => {
    renderSummary();
    const pagings = useEnvironmentRuns.mock.calls.map((call) => call[2]);
    expect(pagings).toContainEqual({ page: 0, pageSize: 10 });
    expect(pagings.every((p) => p.pageSize === 10)).toBe(true);
    // Default "Latest 1 run": only the newest run reaches the graph.
    expect(lastChart().options.xaxis.categories).toHaveLength(1);
  });

  it("pins the runs table's header, since the table scrolls under a fixed graph", () => {
    renderSummary();
    const heads = document.querySelectorAll("thead th");
    expect(heads.length).toBeGreaterThan(0);
    heads.forEach((th) => expect(th).toHaveClass("MuiTableCell-stickyHeader"));
  });
});

// TODO: unskip when compare is shown again.
describe.skip("RunsSummary compare", () => {
  const versioned = { ...env, versioning: { enabled: true } };
  const run = (ordinal, fields = {}) => ({
    ...RUNS[0],
    id: `ex${ordinal}`,
    executionId: `ex${ordinal}`,
    ordinal,
    label: `Run ${ordinal}`,
    runState: "finished",
    ...fields,
  });
  // Run 2 finished with failures: still a finished run, so still comparable.
  const COMPARE_RUNS = [
    run(4),
    run(3, { runState: "running" }),
    run(2, { status: "failed" }),
    run(1),
  ];
  const box = (label) =>
    screen.getByRole("checkbox", { name: `Select ${label}` });
  const compare = () => screen.getByRole("button", { name: "Compare" });

  beforeEach(() => {
    useEnvironmentRuns.mockReset();
    useCompareRuns.mockReset();
    useCompareRuns.mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
    });
  });

  it("lets only finished runs be ticked", () => {
    renderSummary({ env: versioned }, COMPARE_RUNS);
    expect(box("Run 3")).toBeDisabled();
    expect(box("Run 4")).toBeEnabled();
    expect(box("Run 2")).toBeEnabled();
    expect(
      screen.getByText("Select 2 finished runs to compare"),
    ).toBeInTheDocument();
    expect(compare()).toBeDisabled();
  });

  it("enables Compare at two runs and blocks a third", () => {
    renderSummary({ env: versioned }, COMPARE_RUNS);
    fireEvent.click(box("Run 4"));
    expect(screen.getByText("1 of 2 selected")).toBeInTheDocument();
    expect(compare()).toBeDisabled();

    fireEvent.click(box("Run 1"));
    expect(
      screen.getByText("2 of 2 selected. Clear one to pick another"),
    ).toBeInTheDocument();
    expect(compare()).toBeEnabled();
    expect(box("Run 2")).toBeDisabled();

    fireEvent.click(box("Run 4"));
    expect(box("Run 2")).toBeEnabled();
    expect(compare()).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(box("Run 1")).not.toBeChecked();
    expect(
      screen.getByText("Select 2 finished runs to compare"),
    ).toBeInTheDocument();
  });

  it.each(["failed", "cancelled", "cancelling", "queued"])(
    "does not let a %s run be ticked",
    (runState) => {
      renderSummary({ env: versioned }, [run(1, { runState })]);
      expect(box("Run 1")).toBeDisabled();
    },
  );

  it("says why a box is disabled", async () => {
    renderSummary({ env: versioned }, COMPARE_RUNS);
    const hover = (label) =>
      fireEvent.mouseOver(box(label).closest("td").firstChild);

    hover("Run 3");
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Only finished runs can be compared",
    );

    fireEvent.click(box("Run 4"));
    fireEvent.click(box("Run 1"));
    hover("Run 2");
    expect(
      await screen.findByText("2 runs already selected"),
    ).toBeInTheDocument();
  });

  it("will not compare a ticked run that is no longer finished", () => {
    const { rerender } = renderSummary({ env: versioned }, COMPARE_RUNS);
    fireEvent.click(box("Run 4"));
    fireEvent.click(box("Run 1"));
    expect(compare()).toBeEnabled();

    // Re-graded: the run is evaluating again.
    useEnvironmentRuns.mockReturnValue({
      runs: [run(4), run(1, { runState: "running" })],
      count: 2,
      isLoading: false,
    });
    rerender();
    expect(compare()).toBeDisabled();
  });

  it("keeps the selection across pages", () => {
    const firstPage = [run(12), run(11)];
    renderSummary({ env: versioned }, firstPage, false, { count: 12 });
    fireEvent.click(box("Run 12"));
    useEnvironmentRuns.mockImplementation((e, s, { page }) => ({
      runs: page ? [run(1)] : firstPage,
      count: 12,
      isLoading: false,
    }));
    const pager = screen.getByText("1–10 of 12").parentElement;
    fireEvent.click(within(pager).getAllByRole("button").at(-1));

    fireEvent.click(box("Run 1"));
    expect(
      screen.getByText("2 of 2 selected. Clear one to pick another"),
    ).toBeInTheDocument();
    expect(compare()).toBeEnabled();
  });

  it("closes the comparison", async () => {
    renderSummary({ env: versioned }, COMPARE_RUNS);
    fireEvent.click(box("Run 4"));
    fireEvent.click(box("Run 1"));
    fireEvent.click(compare());
    expect(
      screen.getByRole("region", { name: "Compare runs" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Compare runs" })).toBeNull(),
    );
  });

  it("does not open a run when it is ticked", () => {
    const onOpenRun = vi.fn();
    renderSummary({ env: versioned, onOpenRun }, COMPARE_RUNS);
    fireEvent.click(box("Run 4"));
    fireEvent.click(box("Run 4").closest("td"));
    expect(onOpenRun).not.toHaveBeenCalled();
    expect(box("Run 4")).toBeChecked();
  });

  it("compares the newer run against the older, whichever was ticked first", () => {
    renderSummary({ env: versioned }, COMPARE_RUNS);
    fireEvent.click(box("Run 1"));
    fireEvent.click(box("Run 4"));
    expect(useCompareRuns).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      expect.anything(),
      { enabled: true },
    );
    expect(screen.queryByRole("region", { name: "Compare runs" })).toBeNull();

    fireEvent.click(compare());

    expect(
      screen.getByRole("region", { name: "Compare runs" }),
    ).toBeInTheDocument();
    expect(useCompareRuns).toHaveBeenLastCalledWith("env-1", "ex4", "ex1", {
      enabled: true,
    });
  });
});
