import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import PropTypes from "prop-types";
import { useEffect } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const useRunDetail = vi.fn();
const useOptimizationRuns = vi.fn();
const useDebugAnalysis = vi.fn();
const useDrawerRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunDetail: (...args) => useRunDetail(...args),
  useOptimizationRuns: (...args) => useOptimizationRuns(...args),
  useDebugAnalysis: (...args) => useDebugAnalysis(...args),
  useRunCalls: (...args) => useDrawerRunCalls(...args),
}));

const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunCalls: (...args) => useRunCalls(...args),
}));

const useRunReliability = vi.fn();
const exportRunResults = vi.fn();
vi.mock(
  "src/api/simulate-environments/runAnalytics",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useRunReliability: (...args) => useRunReliability(...args),
    exportRunResults: (...args) => exportRunResults(...args),
  }),
);

const listMatchingCalls = vi.fn();
const rerunInRun = vi.fn();
vi.mock(
  "src/api/simulate-environments/rerunScenarios",
  async (importOriginal) => ({
    ...(await importOriginal()),
    listMatchingCalls: (...args) => listMatchingCalls(...args),
    useRerunCallsInRun: () => ({ mutate: rerunInRun, isPending: false }),
  }),
);

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

vi.mock("../../../evals/AddEvaluationDrawer", () => ({ default: () => null }));
vi.mock("../../../evals/AddEvalsDrawer", () => ({ default: () => null }));
vi.mock("../AllEvaluationsDrawer", () => ({ default: () => null }));
vi.mock("../CallDrawer", () => ({ default: () => null }));
vi.mock("../useCallListNavigation", () => ({
  default: () => ({ hasPrev: false, hasNext: false }),
}));

let tickable = true;
function RunTraceTableStub({ onTickableChange }) {
  useEffect(() => {
    onTickableChange?.(tickable);
  }, [onTickableChange]);
  return <div>run-trace-table</div>;
}
RunTraceTableStub.propTypes = { onTickableChange: PropTypes.func };
vi.mock("../trace/RunTraceTable", () => ({ default: RunTraceTableStub }));

let analyticsThrows = false;
function RunAnalyticsStub() {
  if (analyticsThrows) throw new Error("analytics broke");
  return <div>run-analytics</div>;
}
vi.mock("../RunAnalytics", () => ({ default: RunAnalyticsStub }));

const { default: RunDetail } = await import("../RunDetail");

const MINUTE = 60 * 1000;
const ago = (ms) => new Date(Date.now() - ms).toISOString();

const ENV = { id: "env-1", name: "Refund Copilot", platform: {} };

const identity = (overrides = {}) => ({
  id: "ex1",
  executionId: "ex1",
  ordinal: 3,
  letter: "3",
  color: "#7857FC",
  agentVersion: "v2",
  startedAt: ago(12 * MINUTE + 30 * 1000),
  finishedAt: ago(2 * MINUTE),
  status: "passed",
  runState: "finished",
  executionStatus: "completed",
  scenarioIds: undefined,
  trials: 3,
  ...overrides,
});

const stats = (overrides = {}) => ({
  total: 12,
  passed: 8,
  failed: 4,
  errored: 1,
  notScored: 2,
  queued: 0,
  inProgress: 0,
  passRate: 67,
  cost: 1.2,
  scores: {},
  agentType: "voice",
  ...overrides,
});

const RELIABILITY = {
  k: 3,
  pass_hat_1: 66,
  pass_hat_k: 51,
  pass_hat_k_scenarios: 4,
  pass_hat_k_passed: 2,
  pass_hat_k_excluded: 0,
  trials_passed: 8,
  trials_evaluated: 12,
  inconclusive: 1,
  rows: Array.from({ length: 12 }, (_, i) => ({
    scenario: `s${i}`,
    evaluated: true,
  })),
  pass_rate_interval: { low: 55, high: 77, scenarios: 12 },
};

const ANALYSIS = {
  status: "completed",
  isWorking: false,
  errorMessage: null,
  groupingPending: false,
  summary: { measuredCalls: 12, brokenGoals: 1, brokenCalls: 3, oneOffs: 0 },
  goals: [
    {
      goal: "exact_greeting",
      label: "Opens the call with the exact mandatory greeting",
      criteria: null,
      expected: 'the agent says exactly "Hi there."',
      brokenCallIds: ["c1", "c2", "c3"],
      testedCalls: 12,
      ways: [
        {
          id: "cluster-1",
          title: "Prepends Recording Notice",
          phrase: "prepends a recording notice to the greeting",
          callIds: ["c1", "c2", "c3"],
        },
      ],
      unexplainedCallIds: [],
    },
  ],
  oneOffs: [],
};

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  );
}

const renderDetail = ({
  initialEntries = ["/run"],
  initialIndex,
  ...props
} = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={initialEntries} initialIndex={initialIndex}>
        <LocationProbe />
        <RunDetail
          env={ENV}
          envState={{ evals: [] }}
          backed
          testId="rt1"
          executionId="ex1"
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const location = () => screen.getByTestId("location").textContent;

const readBlob = (blob) =>
  new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(blob);
  });

beforeEach(() => {
  tickable = true;
  analyticsThrows = false;
  enqueueSnackbar.mockReset();
  exportRunResults.mockReset();
  exportRunResults.mockResolvedValue(undefined);
  listMatchingCalls.mockReset();
  rerunInRun.mockReset();
  useRunCalls.mockReturnValue({ tasks: [], groups: [], isLoading: false });
  useDrawerRunCalls.mockReturnValue({ tasks: [] });
  useOptimizationRuns.mockReturnValue({ runs: [], isLoading: false });
  useDebugAnalysis.mockReturnValue({
    analysis: ANALYSIS,
    isLoading: false,
    request: vi.fn(),
    isRequesting: false,
    requestError: null,
  });
  useRunReliability.mockReturnValue({ data: RELIABILITY });
  useRunDetail.mockReturnValue({
    identity: identity(),
    stats: stats(),
    isLoading: false,
  });
});

describe.each([
  ["voice", "call"],
  ["text", "conversation"],
])("run detail header on a %s run", (agentType, unit) => {
  it('subline reads "N scenarios × K repeats · 2m ago"; run time and cost sit behind the info icon', async () => {
    const user = userEvent.setup();
    useRunDetail.mockReturnValue({
      identity: identity(),
      stats: stats({ agentType }),
      isLoading: false,
    });
    renderDetail();

    expect(screen.getByText("Run 3")).toBeInTheDocument();
    const subline = screen.getByTestId("run-subline");
    expect(subline).toHaveTextContent(
      "Refund Copilot · agent v2 · 4 scenarios × 3 repeats · 2m ago",
    );
    expect(subline).not.toHaveTextContent(/finished|took|cost/);

    await user.hover(screen.getByLabelText("Run time and cost"));
    expect(await screen.findByText("Took 10m 30s")).toBeInTheDocument();
    expect(screen.getByText("Cost $1.20")).toBeInTheDocument();
  });

  it("the info hover shows a dash for a cost the API doesn't send", async () => {
    const user = userEvent.setup();
    useRunDetail.mockReturnValue({
      identity: identity({ trials: 1 }),
      stats: stats({ agentType, cost: null }),
      isLoading: false,
    });
    renderDetail();

    const subline = screen.getByTestId("run-subline");
    expect(subline).toHaveTextContent("12 scenarios · 2m ago");
    expect(subline).not.toHaveTextContent("repeats");
    await user.hover(screen.getByLabelText("Run time and cost"));
    expect(await screen.findByText("Cost -")).toBeInTheDocument();
  });

  it('a live run shows the progress bar and "x/y done · P passed · F failed · elapsed"', () => {
    useRunDetail.mockReturnValue({
      identity: identity({
        status: "running",
        runState: "running",
        executionStatus: "running",
        stoppable: true,
        startedAt: ago(4 * MINUTE + 12 * 1000),
        finishedAt: null,
      }),
      stats: stats({
        agentType,
        passed: 2,
        failed: 1,
        queued: 6,
        inProgress: 3,
        notScored: 0,
      }),
      isLoading: false,
    });
    renderDetail();

    const bar = screen.getByRole("progressbar", { name: "Run progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByTestId("run-progress")).toHaveTextContent(
      new RegExp(`3/12 ${unit}s done · 2 passed · 1 failed · 4m 1\\ds elapsed`),
    );
    // Live: no finish time and no summary chip yet.
    expect(screen.getByTestId("run-subline")).not.toHaveTextContent("finished");
    expect(screen.queryByTestId("run-summary-chip")).toBeNull();
  });

  it("N not scored note counts error + inconclusive", async () => {
    useRunDetail.mockReturnValue({
      identity: identity(),
      stats: stats({ agentType, notScored: 3 }),
      isLoading: false,
    });
    const user = userEvent.setup();
    renderDetail();

    const note = screen.getByText("3 not scored");
    await user.hover(note);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      `3 ${unit}s ended in an error or without enough evidence to grade`,
    );
  });
});

// A run with no pinned versions re-runs with nothing pre-selected.
const NO_PINS = {
  initialPicks: { agent: undefined, environment: undefined, suite: undefined },
};

describe("run detail header", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides the not scored note when every call was scored", () => {
    useRunDetail.mockReturnValue({
      identity: identity(),
      stats: stats({ notScored: 0 }),
      isLoading: false,
    });
    renderDetail();
    expect(screen.queryByText(/not scored/)).toBeNull();
  });

  it("summary chip shows pass^1 and pass^k; hover card keeps the 95% range; your bar and causes are soon chips", async () => {
    const user = userEvent.setup();
    renderDetail();

    const chip = screen.getByTestId("run-summary-chip");
    expect(chip).toHaveTextContent("66% passed · 51% all 3 repeats");
    await user.hover(chip);
    const card = await screen.findByRole("tooltip");
    expect(card).toHaveTextContent("pass^1");
    expect(card).toHaveTextContent("66%");
    expect(card).toHaveTextContent("pass^3");
    expect(card).toHaveTextContent("51% of 4 scenarios");
    expect(card).toHaveTextContent("95% range");
    expect(card).toHaveTextContent("55%–77%");
    expect(card).toHaveTextContent("Inconclusive");
    expect(within(card).getByText("Your bar soon")).toBeInTheDocument();
    expect(within(card).getByText("Causes soon")).toBeInTheDocument();
  });

  it("summary chip falls back to raw trial counts under 10 scenarios", () => {
    useRunReliability.mockReturnValue({
      data: { ...RELIABILITY, rows: RELIABILITY.rows.slice(0, 4) },
    });
    renderDetail();
    expect(screen.getByTestId("run-summary-chip")).toHaveTextContent(
      "8/12 trials passed",
    );
  });

  it("Re-run what didn't pass re-runs the failed scenario keys with the current repeats; snapshot mode is flagged", async () => {
    listMatchingCalls.mockResolvedValue([
      { id: "c1", sourceScenarioKey: "billing" },
      { id: "c2", sourceScenarioKey: "billing" },
      { id: "c3", sourceScenarioKey: "refund" },
    ]);
    const onStartRun = vi.fn();
    const user = userEvent.setup();
    renderDetail({ onStartRun });

    await user.click(screen.getByRole("button", { name: "Re-run what didn't pass" }));
    await waitFor(() =>
      expect(onStartRun).toHaveBeenCalledWith(["billing", "refund"], 3, NO_PINS),
    );
    expect(listMatchingCalls).toHaveBeenCalledWith("ex1", {
      status: ["failed", "error"],
    });

    await user.click(
      screen.getByRole("button", { name: "More re-run options" }),
    );
    const snapshot = screen.getByRole("menuitem", {
      name: /Re-run exact snapshot/,
    });
    expect(within(snapshot).getByLabelText("UI only")).toBeInTheDocument();
    await user.click(snapshot);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was started.",
      { variant: "info" },
    );
    expect(onStartRun).toHaveBeenCalledTimes(1);
  });

  it("with failures, the caret still re-runs the whole run, as Vel's does", async () => {
    const onStartRun = vi.fn();
    const user = userEvent.setup();
    useRunDetail.mockReturnValue({
      identity: identity({ scenarioIds: ["a", "b"] }),
      stats: stats(),
      isLoading: false,
    });
    renderDetail({ onStartRun });

    await user.click(
      screen.getByRole("button", { name: "More re-run options" }),
    );
    expect(screen.getByText("Re-run run 3 · 2 scenarios")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /default for comparisons/ }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("menuitem", { name: /Run as configured now/ }),
    );
    expect(onStartRun).toHaveBeenCalledWith(["a", "b"], 3, NO_PINS);
    expect(listMatchingCalls).not.toHaveBeenCalled();
  });

  it("re-runs the whole run as configured now when no call carries its scenario", async () => {
    tickable = false;
    const onStartRun = vi.fn();
    const user = userEvent.setup();
    useRunDetail.mockReturnValue({
      identity: identity({ scenarioIds: ["a", "b"] }),
      stats: stats(),
      isLoading: false,
    });
    renderDetail({ onStartRun });

    expect(screen.queryByRole("button", { name: "Re-run what didn't pass" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Re-run" }));
    await user.click(
      screen.getByRole("menuitem", { name: /Run as configured now/ }),
    );
    expect(onStartRun).toHaveBeenCalledWith(["a", "b"], 3, NO_PINS);
    expect(listMatchingCalls).not.toHaveBeenCalled();
  });

  it("keeps the header Re-run on the Analytics tab, where the calls table isn't mounted", () => {
    renderDetail({
      initialEntries: ["/run?tab=analytics"],
      onStartRun: vi.fn(),
    });
    expect(
      screen.getByRole("button", { name: "Re-run what didn't pass" }),
    ).toBeInTheDocument();
  });

  it("offers the whole-run Re-run on a run with nothing failing", () => {
    useRunDetail.mockReturnValue({
      identity: identity(),
      stats: stats({ failed: 0, passed: 12 }),
      isLoading: false,
    });
    renderDetail({ onStartRun: vi.fn() });
    expect(screen.getByRole("button", { name: "Re-run" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Re-run what didn't pass" }),
    ).toBeNull();
  });

  it("Share is an icon button; its menu has Export (real) first, read-only link and PDF flagged", async () => {
    const user = userEvent.setup();
    renderDetail();

    const share = screen.getByRole("button", { name: "Share" });
    expect(share).not.toHaveTextContent("Share");
    await user.click(share);
    expect(
      screen.getByText(/stamped with every version this run held fixed/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /Choose Save as PDF/ }),
    ).toBeInTheDocument();
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveTextContent("Export results");
    expect(within(items[0]).queryByLabelText("UI only")).toBeNull();

    const link = screen.getByRole("menuitem", { name: /Copy read-only link/ });
    const pdf = screen.getByRole("menuitem", { name: /Download PDF/ });
    expect(within(link).getByLabelText("UI only")).toBeInTheDocument();
    expect(within(pdf).getByLabelText("UI only")).toBeInTheDocument();

    await user.click(link);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was saved.",
      { variant: "info" },
    );

    await user.click(screen.getByRole("button", { name: "Share" }));
    await user.click(screen.getByRole("menuitem", { name: /Export results/ }));
    expect(exportRunResults).toHaveBeenCalledWith("ex1");
  });

  it('tabs are "Scenarios (N)" and "Analytics" with ?tab=; a tab that throws shows Retry', async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const user = userEvent.setup();
    renderDetail({ initialEntries: ["/run?tab=analytics"] });

    expect(screen.getByText("run-analytics")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Scenarios (12)" }));
    expect(screen.getByText("run-trace-table")).toBeInTheDocument();
    expect(location()).toBe("/run");

    analyticsThrows = true;
    await user.click(screen.getByRole("tab", { name: "Analytics" }));
    expect(location()).toBe("/run?tab=analytics");
    expect(screen.getByText("This tab failed to load")).toBeInTheDocument();

    analyticsThrows = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("run-analytics")).toBeInTheDocument();
  });

  it("Back returns to the opener", async () => {
    const user = userEvent.setup();
    const { unmount } = renderDetail({
      initialEntries: [
        "/dashboard/simulate/environments/env-1/compare",
        "/run",
      ],
      initialIndex: 1,
    });
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(location()).toBe("/dashboard/simulate/environments/env-1/compare");
    unmount();

    // Opened cold (a pasted link): back goes to the environment's runs.
    renderDetail();
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(location()).toBe("/dashboard/simulate/environments/env-1?tab=runs");
  });

  it("Debug failures adds Export report from the real diagnosis", async () => {
    const createObjectURL = vi.fn(() => "blob:report");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL, revokeObjectURL }),
    );
    // jsdom can't follow a download link.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    await user.click(screen.getByRole("button", { name: "Export report" }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    const text = await readBlob(createObjectURL.mock.calls[0][0]);
    expect(text).toContain("Run 3");
    expect(text).toContain("3 failing of 12 measured");
    expect(text).toContain("Opens the call with the exact mandatory greeting");
    expect(text).toContain("Prepends Recording Notice");
    vi.unstubAllGlobals();
  });

  it("Debug failures has no Export report before the diagnosis is done", async () => {
    useDebugAnalysis.mockReturnValue({
      analysis: { ...ANALYSIS, status: "running", goals: [], oneOffs: [] },
      isLoading: false,
      request: vi.fn(),
      isRequesting: false,
      requestError: null,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    expect(screen.queryByRole("button", { name: "Export report" })).toBeNull();
  });

  it("proof kind, provenance and the re-grade offer appear only with their uiSample features", async () => {
    const { unmount } = renderDetail();
    expect(screen.queryByLabelText(/^Proof/)).toBeNull();
    expect(screen.queryByText(/From CI/)).toBeNull();
    expect(screen.queryByText(/grader fix/)).toBeNull();
    unmount();

    const user = userEvent.setup();
    renderDetail({
      initialEntries: ["/run?uiSample=proofKind,provenance,regrade"],
    });
    expect(screen.getByLabelText("Proof · Sealed")).toBeInTheDocument();
    expect(screen.getByText(/From CI · PR #482/)).toBeInTheDocument();
    expect(screen.getByText(/grader fix/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThanOrEqual(
      3,
    );

    await user.click(
      screen.getByRole("button", { name: "Re-grade 12 scenarios" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was started.",
      { variant: "info" },
    );
  });
});
