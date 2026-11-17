import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// user-info carries the Simulate cutoff flag for the organization.
const authState = { user: {} };
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

const useRunDetail = vi.fn();
const useOptimizationRuns = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRunDetail: (...args) => useRunDetail(...args),
    useOptimizationRuns: (...args) => useOptimizationRuns(...args),
    // The hook's idle view-model (mapDebugAnalysis(null)): FixMyAgentDrawer reads
    // analysis.status unconditionally, so a null analysis is not a state the page sees.
    useDebugAnalysis: () => ({
      analysis: {
        status: "not_requested",
        isWorking: false,
        groupingPending: false,
        errorMessage: null,
        summary: null,
        goals: [],
        oneOffs: [],
      },
      isLoading: false,
      loadError: null,
      request: vi.fn(),
      isRequesting: false,
      requestError: null,
    }),
    useRunCalls: () => ({ tasks: [] }),
    useOptimizerAnalysis: () => ({}),
    useCallExecutionV3Detail: () => ({ data: undefined, error: null }),
  };
});

vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunCalls: () => ({ tasks: [], groups: [], isLoading: false }),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

// The Trials tab needs self-improvement open; Fix my agent stays off here.
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: (feature) => ({
    allowed: feature !== "fix_my_agent",
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("../fixmyagent/selfImprovement", async (importOriginal) => ({
  ...(await importOriginal()),
  useSelfImprovementOpen: () => true,
}));

vi.mock(
  "src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm",
  () => ({ default: () => null }),
);
vi.mock("../../../improvements/LaunchFixDialog", () => ({
  default: () => null,
}));
vi.mock("../../../evals/AddEvaluationDrawer", () => ({ default: () => null }));
vi.mock("../../../evals/AddEvalsDrawer", () => ({ default: () => null }));
vi.mock("../AllEvaluationsDrawer", () => ({ default: () => null }));
vi.mock("../trace/RunTraceTable", () => ({ default: () => null }));
vi.mock("../CallDrawer", () => ({ default: () => null }));
vi.mock("../RunAnalytics", () => ({ default: () => null }));
vi.mock("../useCallListNavigation", () => ({
  default: () => ({
    hasPrev: false,
    hasNext: false,
    onPrev: vi.fn(),
    onNext: vi.fn(),
  }),
}));
vi.mock(
  "src/api/simulate-environments/rerunScenarios",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useResumeRun: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

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
  runState: "finished",
  scenarioIds: ["scenario-a"],
  trials: 1,
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

const OPT_RUN = {
  id: "opt-1",
  name: "Refund fix v1",
  status: "completed",
  optimiserLabel: "ProTeGi",
  trials: 8,
  startedAt: "2026-09-16T09:00:00.000Z",
};

const LINE =
  "Optimization details open in Run Simulation, which your organization doesn't have.";

function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="pathname">{pathname}</div>;
}

const renderDetail = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <LocationProbe />
        <RunDetail
          env={{ id: "env-1", name: "Refund Copilot", platform: {} }}
          envState={{ evals: [] }}
          backed
          testId="rt1"
          executionId="ex1"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const openTrials = async (user) => {
  renderDetail();
  await user.click(screen.getByRole("tab", { name: /Trials \(1\)/ }));
};

describe("RunDetail optimization runs", () => {
  beforeEach(() => {
    authState.user = {};
    useRunDetail.mockReturnValue({
      identity: IDENTITY,
      stats: STATS,
      isLoading: false,
    });
    useOptimizationRuns.mockReturnValue({ runs: [OPT_RUN], isLoading: false });
  });

  it("keeps a row in place, with a line saying why, for an organization that sees only Environments", async () => {
    authState.user = { restrict_simulate_to_environments: true };
    const user = userEvent.setup();
    await openTrials(user);

    expect(screen.getByText(LINE)).toBeInTheDocument();
    await user.click(screen.getByText("Refund fix v1"));

    expect(screen.getByTestId("pathname")).toHaveTextContent(/^\/$/);
  });

  it("opens the optimization run for every other organization", async () => {
    const user = userEvent.setup();
    await openTrials(user);

    expect(screen.queryByText(LINE)).toBeNull();
    await user.click(screen.getByText("Refund fix v1"));

    expect(screen.getByTestId("pathname")).toHaveTextContent(
      "/dashboard/simulate/test/rt1/ex1/opt-1",
    );
  });
});
