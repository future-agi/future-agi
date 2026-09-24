import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearScenarioSelection } from "../../buildEnvironment/console/scenarioSelectionBus";
import {
  MemoryRouter,
  Routes,
  Route,
  useLocation,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// getHarnessJob resolves the harness-backed environment; listHarnessJobs is
// exported for parity with the other consumers of this module.
vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
}));

// Keep the real `endpoints` so the executions URL assertion is genuine; only
// the axios instance is stubbed.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const { getHarnessJob } = await import("src/api/harness/harness");
const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { default: EnvironmentWorkspace } = await import("../EnvironmentWorkspace");
const { default: WorkspaceExecutionDetail } = await import(
  "../runs/WorkspaceExecutionDetail"
);
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);
const { emptyEnvState } = await import("../../store/envState");
const { seedFromTemplate } = await import("../helpers/seedEnvState");
const { PIPELINE_CHECKS_COPY } = await import("../../buildEnvironment/build.constants");

const NOW = "2026-09-15T09:00:00Z";

const TEMPLATE = {
  id: "env-1",
  name: "Refund Copilot",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  tools: [{ name: "issue_refund", desc: "Issues a refund." }],
  rules: ["Never refund twice"],
  seed: { tables: [{ name: "orders", rows: 500 }] },
  evalPreset: ["task_success", "tone"],
};

// A completed harness job with the platform ids at the TOP LEVEL (sibling of
// job/status), the run-test bridge the workspace runs against.
const COMPLETED_JOB = {
  job: {
    job_id: "job-done",
    metadata: { name: "Done Environment" },
    scenario_count: 3,
    agent: { connector: "livekit" },
  },
  status: { stage: "completed", created_at: NOW },
  credentials: { detected_connectors: ["livekit"] },
  platform: { run_test_id: "rt1", test_execution_id: "ex1" },
  stage_outputs: [],
};

// A still-building job: no platform bridge yet, a non-terminal stage. The
// workspace hosts the build experience (pipeline + hero) for it in place.
const BUILDING_JOB = {
  job: {
    job_id: "job-build",
    metadata: { name: "Building Environment" },
    scenario_count: 3,
    agent: { connector: "livekit" },
  },
  // A stage that is genuinely still assembling the world. "running" is stage 10
  // of 14, past connecting_agent, so it reads as built — not what this fixture
  // is for.
  status: { stage: "generating_environment", created_at: NOW },
  credentials: { detected_connectors: ["livekit"] },
  stage_outputs: [],
};

const EXECUTIONS = {
  results: [
    {
      id: "ex1",
      status: "Completed",
      start_time: "2026-01-14T09:12:00.000Z",
      agent_version: "v1",
      total_chats: 12,
      success_rate: 100,
    },
  ],
  count: 1,
};

// The run DETAIL now reads the v3 run-results contract (`runResultsV3.calls`
// returns `{ execution: {...} }`), not the executions list `EXECUTIONS` feeds
// the Runs-tab summary from. The `execution` carries its own server-stamped
// ordinal + agent version — the identity the full-page RunDetail header shows.
const RUN_DETAIL_V3 = {
  execution: {
    id: "ex1",
    ordinal: 1,
    agent_version: "v1",
    agent_type: "VOICE",
    status: "completed",
    started_at: "2026-01-14T09:12:00.000Z",
    completed_at: "2026-01-14T09:22:00.000Z",
    summary: {
      total: 12,
      measured: 12,
      pass_rate: 100,
      outcomes: { passed: 12, failed: 0, error: 0, inconclusive: 0 },
      duration: { average: 50 },
    },
  },
};

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{`${pathname}${search}`}</div>;
}

function renderWorkspace(entry) {
  const client = new QueryClient({
    // harnessJobQuery sets its own `retry` fn (no-retry on 404), which overrides
    // the client default; retryDelay:0 keeps the non-404 retry path instant here.
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <LocationProbe />
        <Routes>
          <Route
            path="/dashboard/simulate/environments/:envId"
            element={<EnvironmentWorkspace />}
          >
            <Route
              path="runs/:testId/:executionId"
              element={<WorkspaceExecutionDetail />}
            />
          </Route>
          <Route
            path="/dashboard/simulate/environments"
            element={<div>environments-list</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const seedClientEnv = (env, envState) =>
  useEnvironmentsStore.setState({
    workspaceEnvs: { [env.id]: env },
    byEnv: { [env.id]: envState },
  });

describe("EnvironmentWorkspace route shell", () => {
  beforeEach(() => {
    // jsdom has no layout, so the console's scroll-to-bottom is a no-op here.
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    // The scenario selection is module-level; clear it so a leaked selection
    // can't bleed into an unrelated test's chat send.
    clearScenarioSelection();
    getHarnessJob.mockReset();
    axios.get.mockReset();
    // The Runs-tab summary reads the executions list; the run DETAIL reads the
    // v3 run-results contract. Route each to its own shape so the full-page
    // RunDetail can resolve its identity header.
    axios.get.mockImplementation((url) =>
      Promise.resolve({
        data:
          url === endpoints.runResultsV3.calls("ex1")
            ? RUN_DETAIL_V3
            : EXECUTIONS,
      }),
    );
  });

  it("renders a seeded client env: name, Live pill and the five tabs", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });

    renderWorkspace("/dashboard/simulate/environments/env-1");

    // The name also appears in the Overview capability graph (an SVG <text>),
    // so scope the header match to its <p>.
    expect(await screen.findByText("Refund Copilot", { selector: "p" }))
      .toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    ["Overview", "Contract", "Scenarios", "Evaluations", "Runs", "Settings"].forEach((label) =>
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument(),
    );
  });

  it("opens the Runs tab from ?tab=runs", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });

    renderWorkspace("/dashboard/simulate/environments/env-1?tab=runs");

    expect(await screen.findByText("Pre-flight")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Runs/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the not-found state for an unknown id", async () => {
    // The axios interceptor rejects with statusCode (not response.status).
    const err = Object.assign(new Error("job not found"), { statusCode: 404 });
    getHarnessJob.mockRejectedValue(err);

    renderWorkspace("/dashboard/simulate/environments/nope");

    expect(await screen.findByText("Environment not found")).toBeInTheDocument();
  });

  it("shows a recoverable error state (not a blank page) when the fetch fails non-404", async () => {
    const err = Object.assign(new Error("Server error"), { statusCode: 500 });
    getHarnessJob.mockRejectedValue(err);

    renderWorkspace("/dashboard/simulate/environments/boom");

    // The error state renders with a Retry — never the silent blank placeholder,
    // and not the 404 "not found" copy.
    expect(await screen.findByText(/Couldn.t load this environment/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Environment not found")).toBeNull();
  });

  it("resolves a harness job: Run enabled, runs listed, row opens the detail", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const user = userEvent.setup();

    renderWorkspace("/dashboard/simulate/environments/job-done?tab=runs");

    // Header Run simulation is enabled for a built harness job.
    const runButton = await screen.findByRole("button", { name: /Run simulation/ });
    await waitFor(() => expect(runButton).toBeEnabled());

    // The populated Runs tab is the summary; its table row for Run 1 opens the
    // run detail on click.
    const row = await screen.findByText(/Run 1 · agent v1/);
    await user.click(row);

    // The row opens the designer-style RunDetail as its own full page: its
    // identity header names the ordinal + agent version and the run status.
    expect(await screen.findByText(/Run 1 · agent v1/)).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-done/runs/rt1/ex1",
    );
  });

  it("renders the run detail as its own full page, without the workspace chrome", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);

    // Deep-link straight to a run: the run detail is the whole page, not a body
    // swapped inside the environment workspace.
    renderWorkspace("/dashboard/simulate/environments/job-done/runs/rt1/ex1");

    // The run detail is shown (its identity header names the ordinal + agent).
    expect(await screen.findByText(/Run 1 · agent v1/)).toBeInTheDocument();

    // ...and the environment-workspace chrome around it is gone: no tab rail and
    // no header "Run simulation" button. The run page owns the viewport.
    expect(screen.queryByRole("tab", { name: /Contract/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Run simulation/ }),
    ).not.toBeInTheDocument();
  });

  it("hosts the build experience in place for a still-building job", async () => {
    getHarnessJob.mockResolvedValue(BUILDING_JOB);

    renderWorkspace("/dashboard/simulate/environments/job-build");

    // The build pipeline + deriving hero render where the workspace tabs will be,
    // reading the same ["harness-job", id] poll the env resolves from.
    expect(await screen.findByText(PIPELINE_CHECKS_COPY.heading)).toBeInTheDocument();
    // The hero shows a neutral skeleton (no derived world yet).
    expect(screen.getByText("0 tools")).toBeInTheDocument();
    // Run stays disabled until the job goes Live.
    expect(screen.getByRole("button", { name: /Run simulation/ })).toBeDisabled();
  });

  it("keeps the build layout but freezes it on a terminal-failed job", async () => {
    getHarnessJob.mockResolvedValue({
      ...BUILDING_JOB,
      job: { ...BUILDING_JOB.job, job_id: "job-failed" },
      status: {
        stage: "failed",
        created_at: NOW,
        failure: { domain: "infrastructure", stage: "understanding_agent", code: "sandbox_launch_failed", message: "Sandbox launch failed" },
      },
    });

    renderWorkspace("/dashboard/simulate/environments/job-failed");

    // The header pill reads Failed (not Building) — but the build layout (chat +
    // pipeline) stays, with the pipeline showing the failure rather than a
    // dead-end error page.
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Building")).toBeNull();
    expect(screen.getByText(PIPELINE_CHECKS_COPY.heading)).toBeInTheDocument();
    expect(screen.getByText("Sandbox launch failed")).toBeInTheDocument();
  });

  it("swaps the build experience for the workspace in place when the job completes", async () => {
    // First poll is still building; the 2s harness-job refetch then lands
    // completed. There is no adopt hand-off — buildStatus flips building→ready
    // off the shared poll and the same url renders the workspace tabs.
    const COMPLETED_BUILD_JOB = {
      ...BUILDING_JOB,
      status: { stage: "completed", created_at: NOW },
      platform: { run_test_id: "rt2", test_execution_id: "ex2" },
    };
    // A mutable stage the shared job poll reads, so the build state is stable
    // until we flip it — then the 2s refetch lands "completed".
    let currentJob = BUILDING_JOB;
    getHarnessJob.mockImplementation(() => Promise.resolve(currentJob));

    renderWorkspace("/dashboard/simulate/environments/job-build");

    // It starts on the build pipeline...
    expect(await screen.findByText(PIPELINE_CHECKS_COPY.heading)).toBeInTheDocument();
    // ...and the placeholder env state is NOT seeded while building, so the real
    // derived world (not the building-time generated pool) wins once it lands.
    expect(useEnvironmentsStore.getState().byEnv["job-build"]).toBeUndefined();

    // ...then the job completes: Run enables (the harness bridge is live) and the
    // build pipeline is gone — the workspace took over in place, same url.
    currentJob = COMPLETED_BUILD_JOB;
    await waitFor(
      () => expect(screen.getByRole("button", { name: /Run simulation/ })).toBeEnabled(),
      { timeout: 6000 },
    );
    expect(screen.queryByText(PIPELINE_CHECKS_COPY.heading)).toBeNull();
    // Env state is seeded now (from the completed job), not before.
    expect(useEnvironmentsStore.getState().byEnv["job-build"]).toBeDefined();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-build",
    );
  }, 12000);

  it("locks a template-seeded env: no overflow, Fork to edit on Overview", async () => {
    seedClientEnv(TEMPLATE, seedFromTemplate(TEMPLATE, NOW));

    renderWorkspace("/dashboard/simulate/environments/env-1");

    expect(await screen.findByText("Refund Copilot", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
    expect(screen.getByRole("button", { name: /Fork to edit/ })).toBeInTheDocument();
  });

  // Fork is temporarily commented out in ForkMenu — re-enable this with it.
  it.skip("forks an unlocked env into a new id and navigates to it", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });
    const user = userEvent.setup();

    renderWorkspace("/dashboard/simulate/environments/env-1");

    await screen.findByText("Refund Copilot", { selector: "p" });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByText("Fork environment"));

    const ids = Object.keys(useEnvironmentsStore.getState().workspaceEnvs);
    const forkId = ids.find((id) => id.startsWith("env-1-fork-"));
    expect(forkId).toBeTruthy();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/simulate/environments/${forkId}`,
    );
  });
});
