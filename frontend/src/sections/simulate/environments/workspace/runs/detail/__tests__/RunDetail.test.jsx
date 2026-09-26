import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import {
  act,
  render,
  screen,
  waitForElementToBeRemoved,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const useRunDetail = vi.fn();
const useOptimizationRuns = vi.fn();
const useDebugAnalysis = vi.fn();
const useRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRunDetail: (...args) => useRunDetail(...args),
    useOptimizationRuns: (...args) => useOptimizationRuns(...args),
    useDebugAnalysis: (...args) => useDebugAnalysis(...args),
    useRunCalls: (...args) => useRunCalls(...args),
  };
});

// Self-improvement is in beta; the launch-flow tests below open it explicitly.
const useSelfImprovementOpen = vi.fn();
vi.mock("../fixmyagent/selfImprovement", async (importOriginal) => ({
  ...(await importOriginal()),
  useSelfImprovementOpen: () => useSelfImprovementOpen(),
}));

// The launch drawer hosts the heavy product optimizer form; stub it to a marker
// that can fire the form's onSuccess (which the real drawer maps to onLaunched).
vi.mock(
  "src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm",
  () => ({
    default: ({ onSuccess }) => (
      <div>
        optimizer-form
        <button type="button" onClick={() => onSuccess?.()}>
          form-success
        </button>
      </div>
    ),
  }),
);

// The picker owns its own network hooks; stub it to a marker that proves the
// run view hands it the execution id (the add goes to the run, not the
// environment). The picker's own behaviour is covered in
// evals/__tests__/addEvaluationDrawer.test.jsx.
//
// Widened to render `completedCallsCount` too, so a wiring bug
// (`RunDetail.jsx` handing the drawer the run's TOTAL call count instead of
// its COMPLETED count) can't hide again — every test in this file would
// have passed with `completedCallsCount={-1}` before this.
function AddEvaluationDrawerStub({ open, executionId, completedCallsCount }) {
  const completed = Number.isFinite(completedCallsCount)
    ? completedCallsCount
    : "unknown";
  return open ? (
    <div>
      add-evals-drawer:{executionId} completed:{completed}
    </div>
  ) : null;
}
AddEvaluationDrawerStub.propTypes = {
  open: PropTypes.bool,
  executionId: PropTypes.string,
  completedCallsCount: PropTypes.number,
};
vi.mock("../../../evals/AddEvaluationDrawer", () => ({
  default: AddEvaluationDrawerStub,
}));

// A non-backed environment (client/template — reachable on this route via
// the `?mockRuns=1` QA switch, which mints run history for any env) gets
// the same store-only picker the Evaluations tab falls back to, not the
// real API picker. Stubbed separately so the two are never confused for one
// another.
function AddEvalsDrawerStub({ open, envState }) {
  return open ? (
    <div>add-evals-drawer-fixture:{(envState?.evals || []).length}</div>
  ) : null;
}
AddEvalsDrawerStub.propTypes = {
  open: PropTypes.bool,
  envState: PropTypes.shape({ evals: PropTypes.array }),
};
vi.mock("../../../evals/AddEvalsDrawer", () => ({
  default: AddEvalsDrawerStub,
}));

// The per-call table owns its own network hook, so stub it to a marker that
// shows the filters it was handed.
function RunTraceTableStub({ initialFilters }) {
  return <div>run-trace-table:{JSON.stringify(initialFilters || {})}</div>;
}
RunTraceTableStub.propTypes = { initialFilters: PropTypes.object };
vi.mock("../trace/RunTraceTable", () => ({ default: RunTraceTableStub }));
vi.mock("../CallDrawer", () => ({
  default: ({ task }) => (task ? <div>{`call-drawer:${task.id}`}</div> : null),
}));

const { default: RunDetail } = await import("../RunDetail");

const IDENTITY = {
  id: "ex1",
  executionId: "ex1",
  ordinal: 3,
  letter: "3",
  color: "#7857FC",
  name: "Refund Copilot",
  agentVersion: "v2",
  startedAt: "2026-09-10T09:00:00.000Z",
  finishedAt: null,
  status: "passed",
  scenarioIds: ["scenario-a", "scenario-b"],
  trials: 3,
};

const STATS = {
  total: 12,
  passed: 8,
  failed: 4,
  passRate: 74,
  durationS: 600,
  avgDurationMs: 50000,
  scores: {},
  failedCritical: 0,
  agentType: "voice",
};

const ENV = { id: "env-1", name: "Refund Copilot", platform: {} };

// A fixture diagnosis view-model (the shape `useDebugAnalysis` returns).
const ANALYSIS = {
  status: "completed",
  isWorking: false,
  errorMessage: null,
  groupingPending: false,
  summary: {
    measuredCalls: 12,
    brokenGoals: 1,
    brokenCalls: 3,
    oneOffs: 1,
    excludedCallIds: [],
    unanalyzedCallIds: [],
  },
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
          callIds: ["c1", "c2"],
        },
        {
          id: "f-3",
          title: "Omits Word Book",
          phrase: "omits the word book",
          callIds: ["c3"],
        },
      ],
      unexplainedCallIds: [],
    },
  ],
  oneOffs: [
    {
      id: "f-9",
      title:
        "In call 11111111-2222-4333-8444-555555555555 the agent never gave the fare",
      phrase: "omitted price information",
      callIds: ["11111111-2222-4333-8444-555555555555"],
    },
  ],
};

const debugHook = (analysis = ANALYSIS, overrides = {}) => ({
  analysis,
  isLoading: false,
  loadError: null,
  request: vi.fn(),
  isRequesting: false,
  requestError: null,
  ...overrides,
});

const OPT_RUN = {
  id: "opt-1",
  name: "Refund fix v1",
  status: "completed",
  optimiserLabel: "ProTeGi",
  trials: 8,
  startedAt: "2026-09-16T09:00:00.000Z",
};

// `backed` defaults to true: every test in this file except the store-only one
// exercises the real (backed) run-detail route, which is what this whole
// suite predates and assumes. `client` lets a test share a spy-wrapped client,
// and any remaining props (e.g. `onStartRun`) pass straight through to RunDetail.
const renderDetail = ({
  backed = true,
  envState = { evals: [] },
  client: passedClient,
  ...props
} = {}) => {
  const client =
    passedClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RunDetail
          env={ENV}
          envState={envState}
          backed={backed}
          testId="rt1"
          executionId="ex1"
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  useSelfImprovementOpen.mockReturnValue(true);
  useRunDetail.mockReturnValue({
    identity: IDENTITY,
    stats: STATS,
    isLoading: false,
  });
  useOptimizationRuns.mockReturnValue({ runs: [], isLoading: false });
  useDebugAnalysis.mockReturnValue(debugHook());
  useRunCalls.mockReturnValue({
    tasks: [
      { id: "c1", scenario: "spanish-billing · Trial 1", status: "passed" },
      { id: "c2", scenario: "spanish-billing · Trial 2", status: "failed" },
      {
        id: "11111111-2222-4333-8444-555555555555",
        scenario: "spanish-meal-policy · Trial 1",
        status: "failed",
      },
    ],
  });
});

describe("RunDetail", () => {
  it("renders the identity header with the real ordinal, agent, status and task count", () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    renderDetail();

    expect(screen.getByText(/Run 3 · agent v2/)).toBeInTheDocument();
    // Some passed, some failed → the run reads "completed", not "Failed".
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText(/Refund Copilot · 12 tasks/)).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Test runs \(12\)/ }),
    ).toBeInTheDocument();
  });

  it("does not show a Failed verdict while the run is still loading", () => {
    // Loading: identity null and zeroed stats must not read as "Failed".
    useRunDetail.mockReturnValue({
      identity: null,
      stats: { total: 0, passed: 0, failed: 0, passRate: 0, scores: {} },
      isLoading: true,
    });
    renderDetail();
    expect(screen.queryByText("Failed")).toBeNull();
  });

  it("shows terminal execution failure despite partial call success", () => {
    useRunDetail.mockReturnValue({
      identity: { ...IDENTITY, status: "failed" },
      stats: STATS,
      isLoading: false,
    });
    renderDetail();

    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("opens the real eval picker from the header action, pointed at this run", async () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    const user = userEvent.setup();
    renderDetail();

    expect(screen.queryByText(/add-evals-drawer/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add evals" }));
    // STATS carries no `completed` field (still loading it) — the drawer
    // must receive no finite count, never a borrowed number.
    expect(
      screen.getByText("add-evals-drawer:ex1 completed:unknown"),
    ).toBeInTheDocument();
  });

  it("hands the picker the run's COMPLETED call count, not its total — the two differ on a run with failures", async () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: { ...STATS, total: 16, failed: 4, completed: 12 },
      isLoading: false,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Add evals" }));
    expect(
      screen.getByText("add-evals-drawer:ex1 completed:12"),
    ).toBeInTheDocument();
  });

  it("hands the picker no finite count while the KPIs are still loading, rather than 0", async () => {
    // `buildRunStats` defaults `total` to 0 before the kpis query resolves;
    // `completed` must stay unknown in that same window, never inherit that
    // placeholder 0.
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: { ...STATS, total: 0 },
      isLoading: true,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Add evals" }));
    expect(
      screen.getByText("add-evals-drawer:ex1 completed:unknown"),
    ).toBeInTheDocument();
  });

  it("falls back to the store-only picker for a non-backed environment reached via ?mockRuns=1", async () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    const user = userEvent.setup();
    renderDetail({ backed: false, envState: { evals: ["preset-eval"] } });

    expect(screen.queryByText(/^add-evals-drawer:/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add evals" }));

    // The store-only fixture picker opens, fed the client envState …
    expect(screen.getByText("add-evals-drawer-fixture:1")).toBeInTheDocument();
    // … and the real API picker never mounts — it would 404/error against a
    // client-minted id that has no `/harness-environments/{id}/` backend.
    expect(screen.queryByText(/^add-evals-drawer:/)).toBeNull();
  });

  it("invalidates the optimization runs on launch without a detached-client crash", async () => {
    // refetchOptimizations was a detached invalidateQueries, which throws on
    // this.#queryCache in react-query v5.
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const user = userEvent.setup();
    renderDetail({ client });

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    await user.click(
      screen.getByRole("button", { name: "Run Self Improvement" }),
    );
    // The optimizer form's onSuccess flows through the real launch drawer to
    // onLaunched → queryClient.invalidateQueries.
    await user.click(screen.getByRole("button", { name: "form-success" }));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["agent-optimization-runs", "ex1"],
    });
  });

  it("submits the same immutable selection and trials on Run again", async () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    const user = userEvent.setup();
    const onStartRun = vi.fn();
    renderDetail({ onStartRun });

    await user.click(screen.getByRole("button", { name: "Run again" }));

    expect(onStartRun).toHaveBeenCalledWith(["scenario-a", "scenario-b"], 3);
  });

  it("does not invent a critical-failure classification", () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    renderDetail();

    expect(screen.queryByText(/critical/)).toBeNull();
  });

  it("opens the Debug-failures drawer on the goals the evals say broke", async () => {
    const user = userEvent.setup();
    renderDetail();

    expect(screen.queryByText(/exact mandatory greeting/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    expect(
      screen.getByText(
        "Your agent broke 1 goal on 3 of 12 calls, plus 1 one-off issue.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Opens the call with the exact mandatory greeting"),
    ).toBeInTheDocument();
    // A count beside a goal worded as success must read as the failure it is.
    expect(screen.getByText("Broke on 3 of 12 calls")).toBeInTheDocument();
    expect(
      screen.getByText('Passes when the agent says exactly "Hi there."'),
    ).toBeInTheDocument();
    expect(screen.getByText("Omits the word book")).toBeInTheDocument();
    // One-offs keep Omega's prose, with its call ids read as the call's label.
    expect(screen.getByText("Also seen, 1 call each")).toBeInTheDocument();
    expect(
      screen.getByText(
        'In call "spanish-meal-policy · Trial 1" the agent never gave the fare',
      ),
    ).toBeInTheDocument();
    // Nothing is framed as the user's evals falling short.
    expect(screen.queryByText(/Missed by your evals/)).toBeNull();
  });

  it("heads the drawer with the diagnosis's own counts once it has them", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    // The run reports 4 failing, but the diagnosis measured 3 broken of 12.
    expect(screen.getByText("3 failing of 12 measured")).toBeInTheDocument();
  });

  it("says when calls did not complete, without blaming anyone", async () => {
    useDebugAnalysis.mockReturnValue(
      debugHook({
        ...ANALYSIS,
        summary: { ...ANALYSIS.summary, excludedCallIds: ["c7", "c8", "c9"] },
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    const line = screen.getByText(
      /^3 calls didn't complete, so they aren't counted\./,
    );
    // Neutral about why: the drawer never blames the platform.
    expect(screen.queryByText(/our side/)).toBeNull();
    // The exclusion can be checked, not just taken on trust.
    await user.click(within(line).getByRole("button", { name: "View" }));

    expect(
      screen.getByText('run-trace-table:{"callExecutionId":["c7","c8","c9"]}'),
    ).toBeInTheDocument();
  });

  it("folds long tails into one row so the rows still add up", async () => {
    const ways = Array.from({ length: 7 }, (_, i) => ({
      id: `w${i}`,
      title: `Way ${i}`,
      phrase: `way number ${i}`,
      callIds: [`c${i}`],
    }));
    useDebugAnalysis.mockReturnValue(
      debugHook({
        ...ANALYSIS,
        goals: [
          {
            ...ANALYSIS.goals[0],
            ways,
            brokenCallIds: ways.map((w) => w.callIds[0]),
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    expect(screen.getByText("Way number 2")).toBeInTheDocument();
    expect(screen.queryByText("Way number 3")).toBeNull();
    const fold = screen.getByRole("button", { name: "4 other ways" });
    expect(fold.closest("div").parentElement).toHaveTextContent(/^4/);

    await user.click(fold);
    expect(screen.getByText("Way number 6")).toBeInTheDocument();
  });

  it("says when some calls could not be analysed", async () => {
    const request = vi.fn();
    useDebugAnalysis.mockReturnValue(
      debugHook(
        {
          ...ANALYSIS,
          summary: { ...ANALYSIS.summary, unanalyzedCallIds: ["c4"] },
        },
        { request },
      ),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    const line = screen.getByText(
      /^1 call hasn't been analysed yet, so some issues may be missing\./,
    );
    expect(screen.queryByText(/didn't complete/)).toBeNull();
    // Most unread calls read fine on a second pass.
    await user.click(within(line).getByRole("button", { name: "Try again" }));
    expect(request).toHaveBeenCalled();
  });

  it("hands a goal's broken calls to the calls table", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    await user.click(screen.getByRole("button", { name: "View 3 calls" }));

    expect(
      screen.getByText('run-trace-table:{"callExecutionId":["c1","c2","c3"]}'),
    ).toBeInTheDocument();
    // The drawer closes (after its exit transition) so the table is in view.
    await waitForElementToBeRemoved(() =>
      screen.queryByText(/exact mandatory greeting/),
    );
  });

  it("requests the diagnosis once when the drawer opens on an unanalyzed run", async () => {
    const hook = debugHook({
      ...ANALYSIS,
      status: "not_requested",
      goals: [],
      oneOffs: [],
    });
    useDebugAnalysis.mockReturnValue(hook);
    const user = userEvent.setup();
    renderDetail();

    expect(hook.request).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    expect(hook.request).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Analyzing…");
    expect(
      screen.queryByRole("button", { name: "Run Self Improvement" }),
    ).toBeNull();
  });

  it("tells the user when the diagnosis is taking unusually long", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      useDebugAnalysis.mockReturnValue(
        debugHook({ ...ANALYSIS, status: "running", isWorking: true }),
      );
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderDetail();
      await user.click(screen.getByRole("button", { name: "Debug failures" }));

      expect(
        screen.getByText("Omega is reading every call in this run"),
      ).toBeInTheDocument();
      await act(async () => {
        vi.advanceTimersByTime(4 * 60 * 1000 + 1);
      });
      expect(
        screen.getByText(/taking longer than expected/),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a refused request as an error with a retry, not an endless loader", async () => {
    const hook = debugHook(
      { ...ANALYSIS, status: "not_requested", goals: [], oneOffs: [] },
      {
        requestError: {
          code: "execution_not_completed",
          detail: "Test execution contains nonterminal calls",
        },
      },
    );
    useDebugAnalysis.mockReturnValue(hook);
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    expect(
      screen.getByText(
        "Diagnosis is available once every call in this run finishes.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/nonterminal/)).toBeNull();
    // One automatic request on open, then one from the retry.
    expect(hook.request).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(hook.request).toHaveBeenCalledTimes(2);
  });

  it("launches the first optimization from the diagnosis footer, with no prior runs", async () => {
    useOptimizationRuns.mockReturnValue({ runs: [], isLoading: false });
    const user = userEvent.setup();
    renderDetail();

    // No Trials tab yet (no prior runs), so the footer CTA is the only launch path.
    expect(screen.queryByRole("tab", { name: /Trials/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    const cta = screen.getByRole("button", { name: "Run Self Improvement" });
    expect(cta).toBeInTheDocument();

    await user.click(cta);
    expect(screen.getByText("optimizer-form")).toBeInTheDocument();
  });

  it("shows the Imagine tab as coming soon (no backend)", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    await user.click(screen.getByText("Imagine"));
    expect(screen.getByLabelText("Coming soon")).toBeInTheDocument();
  });

  it("keeps self-improvement in beta: its launch is shown but disabled", async () => {
    useSelfImprovementOpen.mockReturnValue(false);
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Debug failures" }));
    const cta = screen.getByRole("button", { name: /Run Self Improvement/ });
    expect(cta).toBeDisabled();
    expect(cta).toHaveTextContent("BETA");
  });

  it("keeps the Trials tab visible but disabled while self-improvement is in beta", () => {
    useSelfImprovementOpen.mockReturnValue(false);
    useOptimizationRuns.mockReturnValue({ runs: [OPT_RUN], isLoading: false });
    renderDetail();

    const trialsTab = screen.getByRole("tab", { name: /Trials \(1\)/ });
    expect(trialsTab).toBeDisabled();
    expect(trialsTab).toHaveTextContent("BETA");
  });

  it("omits the Trials tab when there are no optimization runs", () => {
    useOptimizationRuns.mockReturnValue({ runs: [], isLoading: false });
    renderDetail();

    expect(screen.queryByRole("tab", { name: /Trials/ })).toBeNull();
  });

  it("shows the Trials tab and its runs when optimizations exist", async () => {
    useOptimizationRuns.mockReturnValue({ runs: [OPT_RUN], isLoading: false });
    const user = userEvent.setup();
    renderDetail();

    const trialsTab = screen.getByRole("tab", { name: /Trials \(1\)/ });
    expect(trialsTab).toBeInTheDocument();
    await user.click(trialsTab);
    expect(screen.getByText("Refund fix v1")).toBeInTheDocument();
    expect(screen.getByText(/ProTeGi · 8 trials/)).toBeInTheDocument();
  });
});
