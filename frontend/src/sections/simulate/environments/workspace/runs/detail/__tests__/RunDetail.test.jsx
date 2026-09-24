import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { useEffect } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { paths } from "src/routes/paths";

// The run-level data hooks are mocked so the render tests assert the wiring
// against fixed view-models rather than the network. `runSimulationTarget` is
// kept real so the Run-again navigation target is genuine.
const useRunDetail = vi.fn();
const useOptimizationRuns = vi.fn();
const useOptimizerAnalysis = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRunDetail: (...args) => useRunDetail(...args),
    useOptimizationRuns: (...args) => useOptimizationRuns(...args),
    useOptimizerAnalysis: (...args) => useOptimizerAnalysis(...args),
  };
});

// The launch drawer hosts the heavy product optimizer form; stub it to a marker.
vi.mock("src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm", () => ({
  default: () => <div>optimizer-form</div>,
}));

// The picker owns its own network hooks; stub it to a marker that proves the
// run view hands it the execution id (§6 — the add goes to the run, not the
// environment). The picker's own behaviour is covered in
// evals/__tests__/addEvaluationDrawer.test.jsx.
// M1 (round 2): widened to render `completedCallsCount` too, so the wiring
// bug this test file didn't catch (`RunDetail.jsx` handing the drawer the
// run's TOTAL call count instead of its COMPLETED count, P27) can't hide
// again — every test in this file would have passed with
// `completedCallsCount={-1}` before this.
function AddEvaluationDrawerStub({ open, executionId, completedCallsCount }) {
  const completed = Number.isFinite(completedCallsCount) ? completedCallsCount : "unknown";
  return open ? <div>add-evals-drawer:{executionId} completed:{completed}</div> : null;
}
AddEvaluationDrawerStub.propTypes = {
  open: PropTypes.bool,
  executionId: PropTypes.string,
  completedCallsCount: PropTypes.number,
};
vi.mock("../../../evals/AddEvaluationDrawer", () => ({ default: AddEvaluationDrawerStub }));

// L6: a non-backed environment (client/template — reachable on this route via
// the `?mockRuns=1` QA switch, which mints run history for any env) gets the
// same store-only picker the Evaluations tab falls back to, not the real API
// picker. Stubbed separately so the two are never confused for one another.
function AddEvalsDrawerStub({ open, envState }) {
  return open ? <div>add-evals-drawer-fixture:{(envState?.evals || []).length}</div> : null;
}
AddEvalsDrawerStub.propTypes = {
  open: PropTypes.bool,
  envState: PropTypes.shape({ evals: PropTypes.array }),
};
vi.mock("../../../evals/AddEvalsDrawer", () => ({ default: AddEvalsDrawerStub }));

// The per-call table owns its own network hook; stub it and drive the
// failed-critical seam (which feeds the header banner) from a controllable value.
const traceState = vi.hoisted(() => ({ failedCritical: 0 }));
function RunTraceTableStub({ onFailedCriticalChange }) {
  useEffect(() => {
    onFailedCriticalChange?.(traceState.failedCritical);
  }, [onFailedCriticalChange]);
  return <div>run-trace-table</div>;
}
RunTraceTableStub.propTypes = { onFailedCriticalChange: PropTypes.func };
vi.mock("../trace/RunTraceTable", () => ({ default: RunTraceTableStub }));

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
  status: "failed",
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
};

const ENV = { id: "env-1", name: "Refund Copilot", platform: {} };

// A fixture diagnosis view-model (the shape `useOptimizerAnalysis` returns).
const ANALYSIS = {
  status: "completed",
  isWorking: false,
  hasResponse: true,
  summary: "The agent skips the refund-eligibility check.",
  humanComparison: null,
  lastUpdated: "2026-09-15T10:00:00.000Z",
  fixable: [
    {
      id: "agent:0",
      heading: "Confirm eligibility before refunding",
      recommendation: "Add an explicit eligibility gate.",
      breakingPoints: ["Refunded an out-of-window order"],
      priority: "high",
      callExecutionIds: ["c1", "c2"],
      callsAffected: 2,
      branchCategory: "Refunds",
      level: "agent",
    },
  ],
  environmental: [],
};

const OPT_RUN = {
  id: "opt-1",
  name: "Refund fix v1",
  status: "completed",
  optimiserLabel: "ProTeGi",
  trials: 8,
  startedAt: "2026-09-16T09:00:00.000Z",
};

function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="location">{pathname}</div>;
}

// `backed` defaults to true: every test in this file except the L6 one below
// exercises the real (backed) run-detail route, which is what this whole
// suite predates and assumes.
const renderDetail = ({ backed = true, envState } = {}) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LocationProbe />
        <RunDetail env={ENV} envState={envState} backed={backed} testId="rt1" executionId="ex1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
  useOptimizationRuns.mockReturnValue({ runs: [], isLoading: false });
  useOptimizerAnalysis.mockReturnValue({
    analysis: ANALYSIS,
    isLoading: false,
    refresh: vi.fn(),
    isRefreshing: false,
  });
});

describe("RunDetail", () => {
  it("renders the identity header with the real ordinal, agent, status and task count", () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
    renderDetail();

    expect(screen.getByText(/Run 3 · agent v2/)).toBeInTheDocument();
    // Some passed, some failed → the run reads "completed", not "Failed".
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText(/Refund Copilot · 12 tasks/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Test runs \(12\)/ })).toBeInTheDocument();
  });

  it("opens the real eval picker from the header action, pointed at this run", async () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
    const user = userEvent.setup();
    renderDetail();

    expect(screen.queryByText(/add-evals-drawer/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add evals" }));
    // STATS carries no `completed` field (still loading it) — the drawer must
    // receive no finite count, never a borrowed number (M1, round 2).
    expect(screen.getByText("add-evals-drawer:ex1 completed:unknown")).toBeInTheDocument();
  });

  it("hands the picker the run's COMPLETED call count, not its total — the two differ on a run with failures (M1, round 2, P27/P19)", async () => {
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: { ...STATS, total: 16, failed: 4, completed: 12 },
      isLoading: false,
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Add evals" }));
    expect(screen.getByText("add-evals-drawer:ex1 completed:12")).toBeInTheDocument();
  });

  it("hands the picker no finite count while the KPIs are still loading, rather than 0 (M1, round 2)", async () => {
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
    expect(screen.getByText("add-evals-drawer:ex1 completed:unknown")).toBeInTheDocument();
  });

  it("falls back to the store-only picker for a non-backed environment reached via ?mockRuns=1 (L6)", async () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
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

  it("navigates to the run-simulation target on Run again", async () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Run again" }));
    // ENV carries no platform bridge → the product Run-Simulation entry.
    expect(screen.getByTestId("location")).toHaveTextContent(paths.dashboard.simulate.test);
  });

  it("shows the critical-failure banner from the per-call table's failed-critical count", () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
    traceState.failedCritical = 2;
    renderDetail();

    expect(screen.getByText(/2 critical scenarios failed/)).toBeInTheDocument();
    traceState.failedCritical = 0;
  });

  it("hides the critical-failure banner when no critical calls failed", () => {
    useRunDetail.mockReturnValue({ identity: IDENTITY, stats: STATS, isLoading: false });
    traceState.failedCritical = 0;
    renderDetail();

    expect(screen.queryByText(/critical/)).toBeNull();
  });

  it("opens the Debug-failures drawer and renders the real diagnosis", async () => {
    const user = userEvent.setup();
    renderDetail();

    expect(screen.queryByText("Confirm eligibility before refunding")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Debug failures" }));

    // The drawer header + the diagnosis summary and the fixture recommendation.
    expect(screen.getByText(/4 failing of 12 measured/)).toBeInTheDocument();
    expect(screen.getByText(/skips the refund-eligibility check/)).toBeInTheDocument();
    expect(screen.getByText("Confirm eligibility before refunding")).toBeInTheDocument();
    expect(screen.getByText("High priority")).toBeInTheDocument();
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
