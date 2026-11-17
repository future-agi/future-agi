import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ user: {} }),
}));

const useRunDetail = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useRunDetail: (...args) => useRunDetail(...args),
    useOptimizationRuns: () => ({ runs: [], isLoading: false }),
    // The hook's idle view-model: FixMyAgentDrawer reads analysis.status
    // unconditionally, so a null analysis is not a state the page sees.
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
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: false,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("../fixmyagent/selfImprovement", async (importOriginal) => ({
  ...(await importOriginal()),
  useSelfImprovementOpen: () => false,
}));
// Stubbed whole, as the other run page tests do: the optimization form is
// never opened here, and its module is never loaded.
vi.mock(
  "src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm",
  () => ({ default: () => null }),
);
vi.mock("../../../improvements/LaunchFixDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../../evals/AddEvaluationDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../../../evals/AddEvalsDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../AllEvaluationsDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../trace/RunTraceTable", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../CallDrawer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../RunAnalytics", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../ReliabilityHeadline", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../useCallListNavigation", async (importOriginal) => ({
  ...(await importOriginal()),
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

const UNAVAILABLE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";

const FAILED_RUN = {
  id: "ex1",
  executionId: "ex1",
  ordinal: 4,
  letter: "4",
  color: "#7857FC",
  name: "Refund Copilot",
  agentVersion: "v2",
  startedAt: "2026-09-10T09:00:00.000Z",
  finishedAt: null,
  status: "failed",
  runState: "failed",
  executionStatus: "failed",
  stoppable: false,
  scenarioIds: ["scenario-a"],
  trials: 1,
};

const STATS = {
  total: 0,
  passed: 0,
  failed: 0,
  passRate: 0,
  durationS: null,
  avgDurationMs: null,
  scores: {},
  failedCritical: 0,
  agentType: "voice",
};

const renderFailedRun = (failure) => {
  useRunDetail.mockReturnValue({
    identity: { ...FAILED_RUN, failure },
    stats: STATS,
    isLoading: false,
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
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

describe("RunDetail for a failed run", () => {
  beforeEach(() => {
    useRunDetail.mockReset();
  });

  it("says why a run of a private GitHub repository failed, under its status", () => {
    renderFailedRun({
      domain: "environment",
      code: "github_private_unavailable",
    });

    expect(screen.getByTestId("run-failure-reason")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
  });

  it("keeps any other failure to its status alone, as before", () => {
    renderFailedRun({ domain: "infrastructure", code: "hosted_launch_failed" });

    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("run-failure-reason")).toBeNull();
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });

  it("adds no line when the server sends no reason", () => {
    renderFailedRun(undefined);

    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("run-failure-reason")).toBeNull();
  });

  it.each(["constructor", "__proto__", "toString"])(
    "keeps a failure coded with a name every object has to its status alone (%s)",
    (code) => {
      renderFailedRun({ domain: "agent", code });

      expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
      expect(screen.queryByTestId("run-failure-reason")).toBeNull();
    },
  );
});
