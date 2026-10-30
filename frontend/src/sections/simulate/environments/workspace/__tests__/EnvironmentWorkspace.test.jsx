import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
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
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
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
const { SAVE_GRACE_MS } = await import(
  "src/api/simulate-environments/environment"
);

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

const JOB_SCENARIO = {
  scenario_key: "refund-happy-path",
  name: "refund_happy_path",
  instruction: "Ask for a refund on a delivered order.",
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
  // Run needs scenarios, and a real env takes them only from the run's own
  // data (no fixture pool), so the completed job carries one.
  scenarios: [JOB_SCENARIO],
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

// The completed job's environment detail before the build authored a contract:
// it loads, but every section is still empty, so the job poll fills the page.
const DETAIL_NOT_AUTHORED = {
  id: "job-done",
  overview: { id: "job-done", name: "Done Environment" },
  contract: null,
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
    // RunDetail can resolve its identity header. The environment detail
    // (GET /harness-environments/{id}/) answers for the two known jobs with no
    // contract authored, so these tests exercise the job-poll path; any other
    // id 404s, since answering it would make every unknown id resolve to a
    // garbage environment.
    axios.get.mockImplementation((url) => {
      const known = url.match(/\/harness-environments\/(job-done|job-build)\/$/);
      if (known) {
        return Promise.resolve({
          data: { ...DETAIL_NOT_AUTHORED, id: known[1], overview: { id: known[1] } },
        });
      }
      if (/\/harness-environments\/[^/]+\/$/.test(url)) {
        return Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 }));
      }
      return Promise.resolve({
        data:
          url === endpoints.runResultsV3.calls("ex1")
            ? RUN_DETAIL_V3
            : EXECUTIONS,
      });
    });
  });

  it("renders a seeded client env: name, Live pill and its tabs, Runs included before a run", async () => {
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
    expect(screen.getByText("Ready")).toBeInTheDocument();
    ["Overview", "World", "Contract", "Scenarios", "Evaluations", "Runs", "Settings"].forEach((label) =>
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument(),
    );
  });

  it("opens ?tab=runs on the Runs empty state while the env has no runs", async () => {
    seedClientEnv(TEMPLATE, {
      ...emptyEnvState(),
      agent: { name: "Support agent" },
      scenarios: [{ id: "s1" }],
    });

    renderWorkspace("/dashboard/simulate/environments/env-1?tab=runs");

    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Runs/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Pre-flight")).toBeNull();
  });

  it("shows the app loader, not a blank page, while the environment is being resolved", async () => {
    getHarnessJob.mockReturnValue(new Promise(() => {}));
    const base = axios.get.getMockImplementation();
    axios.get.mockImplementation((url) =>
      /\/harness-environments\/job-pending\/$/.test(url) ? new Promise(() => {}) : base(url),
    );

    renderWorkspace("/dashboard/simulate/environments/job-pending");

    expect(await screen.findByText("Preparing for liftoff")).toBeInTheDocument();
    expect(screen.queryByText("Environment not found")).toBeNull();
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

  it("offers a retry instead of half-filled tabs when the environment detail fails", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    let detailCalls = 0;
    const base = axios.get.getMockImplementation();
    axios.get.mockImplementation((url) => {
      if (/\/harness-environments\/job-done\/$/.test(url)) {
        detailCalls += 1;
        // Fails until the user retries; the query's own retries count too.
        return detailCalls <= 4
          ? Promise.reject(Object.assign(new Error("Server error"), { statusCode: 500 }))
          : Promise.resolve({ data: DETAIL_NOT_AUTHORED });
      }
      return base(url);
    });
    const user = userEvent.setup();

    renderWorkspace("/dashboard/simulate/environments/job-done");

    expect(
      await screen.findByText(/Couldn.t load this environment.s details/, {}, { timeout: 8000 }),
    ).toBeInTheDocument();
    // None of the tabs render on the job poll alone: no guessed channel, no
    // "No agent attached", no sample cards.
    expect(screen.queryByRole("tab", { name: /Overview/ })).toBeNull();
    expect(screen.queryByText("No agent attached")).toBeNull();
    // Nor can a run start without them.
    expect(screen.getByRole("button", { name: /Run simulation/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("tab", { name: /Overview/ })).toBeInTheDocument();
    expect(screen.queryByText(/Couldn.t load this environment.s details/)).toBeNull();
  }, 15000);

  it("holds the tabs back, and Run with them, while the environment detail is on its way", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const base = axios.get.getMockImplementation();
    axios.get.mockImplementation((url) =>
      /\/harness-environments\/job-done\/$/.test(url) ? new Promise(() => {}) : base(url),
    );

    // A revisit: this environment's agent and scenarios are already in the
    // client store, so Run would be ready on its own; only the missing detail
    // holds it back.
    useEnvironmentsStore.setState({
      byEnv: {
        "job-done": {
          ...emptyEnvState(),
          agent: { typeId: "livekit", via: "endpoint", values: {} },
          scenarios: [{ id: "scenario-a", name: "scenario-a" }],
        },
      },
    });

    renderWorkspace("/dashboard/simulate/environments/job-done");

    const runButton = await screen.findByRole("button", { name: /Run simulation/ });
    expect(runButton).toBeDisabled();
    expect(screen.queryByRole("tab", { name: /Overview/ })).toBeNull();
    expect(screen.queryByText(/^· \d+ scenarios?$/)).toBeNull();
    // The tab area shows the loader rather than an empty pane.
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  it("counts scenarios from the environment detail, not the seeded job list", async () => {
    // The job poll seeds no scenarios; the detail says the suite has 3.
    getHarnessJob.mockResolvedValue({ ...COMPLETED_JOB, scenarios: [] });
    const base = axios.get.getMockImplementation();
    axios.get.mockImplementation((url) =>
      /\/harness-environments\/job-done\/$/.test(url)
        ? Promise.resolve({
            data: { ...DETAIL_NOT_AUTHORED, overview: { id: "job-done", scenario_count: 3 } },
          })
        : base(url),
    );

    renderWorkspace("/dashboard/simulate/environments/job-done");

    const runButton = await screen.findByRole("button", { name: /Run simulation/ });
    await waitFor(() => expect(runButton).toBeEnabled());
    expect(await screen.findByRole("tab", { name: /Scenarios\s*3/ })).toBeInTheDocument();
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
    // header names the ordinal, with the agent version on the line below.
    expect((await screen.findAllByText("Run 1")).length).toBeGreaterThan(0);
    // The run's Passed status shows (the header's verdict moved into the
    // summary chip in Vel's layout).
    expect(screen.getAllByText("Passed").length).toBeGreaterThan(0);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-done/runs/rt1/ex1",
    );
  });

  it("keeps the collapsed chat's dot on while the builder waits on a question", async () => {
    const question = {
      state: "waiting_for_user",
      runtime: { available: true },
      blocking_input: { message_id: "q1", kind: "question_requested", prompt: "How strict?" },
      messages: [{ message_id: "q1", role: "assistant", kind: "question", state: "completed", content: "How strict?", created_at: NOW, sequence: 1 }],
      events: [],
    };
    window.localStorage.setItem("simEnv.chatPane.collapsed", "true");
    try {
      getHarnessJob.mockResolvedValue({ ...COMPLETED_JOB, conversation: question });
      const first = renderWorkspace("/dashboard/simulate/environments/job-done");
      expect(await screen.findByTestId("chat-split-busy")).toBeInTheDocument();
      first.unmount();

      // Same question, but the chat can't take an answer any more: no dot.
      getHarnessJob.mockResolvedValue({
        ...COMPLETED_JOB,
        conversation: { ...question, runtime: { available: false } },
      });
      renderWorkspace("/dashboard/simulate/environments/job-done");
      // The frozen chat says why it can't take messages; by then the state is in.
      expect((await screen.findAllByText(/no saved workspace/i)).length).toBeGreaterThan(0);
      expect(screen.queryByTestId("chat-split-busy")).not.toBeInTheDocument();
    } finally {
      window.localStorage.removeItem("simEnv.chatPane.collapsed");
    }
  });

  it("keeps the collapsed chat's dot on while a nudge waits for an answer", async () => {
    const conversation = {
      state: "warm_idle",
      runtime: { available: true },
      messages: [{ message_id: "m1", role: "system", kind: "status", state: "completed", content: "Agent v2 changed: prompt. Rebuild the environment?", payload: { nudge_id: "n1", nudge_target: "environment" }, created_at: NOW, sequence: 1 }],
      events: [],
    };
    const nudgesAre = (state) =>
      axios.get.mockImplementation((url) => {
        if (url.endsWith("/nudges/")) {
          return Promise.resolve({ data: [{ id: "n1", target: "environment", state, facts: {}, card_message_id: "m1" }] });
        }
        return Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 }));
      });
    const job = { ...COMPLETED_JOB, versioning: { enabled: true, nudges_enabled: true }, conversation };
    window.localStorage.setItem("simEnv.chatPane.collapsed", "true");
    try {
      nudgesAre("pending");
      getHarnessJob.mockResolvedValue(job);
      const first = renderWorkspace("/dashboard/simulate/environments/job-done");
      expect(await screen.findByTestId("chat-split-busy")).toBeInTheDocument();
      first.unmount();

      // Skipped: nothing waits on the user any more.
      nudgesAre("answered_no");
      renderWorkspace("/dashboard/simulate/environments/job-done");
      // The card shows the skip once the list is in.
      expect(
        await screen.findByText("Skipped. The current version stays."),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("chat-split-busy")).not.toBeInTheDocument();
    } finally {
      window.localStorage.removeItem("simEnv.chatPane.collapsed");
    }
  });

  it("offers Stop for a rebuild waiting on an answer without saying the chat is working, and holds it while it stops", async () => {
    // The builder's question pauses the turn; its command stays delivered.
    const conversation = {
      state: "waiting_for_user",
      runtime: { available: true },
      blocking_input: { message_id: "q1", kind: "question_requested" },
      messages: [
        {
          message_id: "m1",
          role: "system",
          kind: "status",
          state: "completed",
          content: "Agent v2 changed: prompt. Rebuild the environment?",
          payload: { nudge_id: "n1", nudge_target: "environment" },
          created_at: NOW,
          sequence: 1,
        },
        {
          message_id: "u1",
          role: "user",
          kind: "message",
          state: "delivered",
          content: "Add a rule",
          created_at: NOW,
          sequence: 2,
        },
        {
          message_id: "q1",
          role: "assistant",
          kind: "question",
          state: "completed",
          content: "How strict?",
          created_at: NOW,
          sequence: 3,
        },
      ],
      events: [],
    };
    axios.get.mockImplementation((url) => {
      if (url.endsWith("/nudges/")) {
        return Promise.resolve({
          data: [
            {
              id: "n1",
              target: "environment",
              state: "queued",
              queued_behind_input: true,
              facts: {},
              card_message_id: "m1",
            },
          ],
        });
      }
      return Promise.reject(
        Object.assign(new Error("Not found"), { statusCode: 404 }),
      );
    });
    getHarnessJob.mockResolvedValue({
      ...COMPLETED_JOB,
      versioning: { enabled: true, nudges_enabled: true },
      conversation,
    });
    renderWorkspace("/dashboard/simulate/environments/job-done");
    expect(
      await screen.findByText("Waiting for your answer in chat"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(
      screen.getByText("Ask, correct, or steer what it builds"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Working…")).not.toBeInTheDocument();

    axios.post.mockReturnValue(new Promise(() => {}));
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/job-done/nudges/n1/stop/",
      {},
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled(),
    );
  });

  it("renders the run detail as its own full page, without the workspace chrome", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);

    // Deep-link straight to a run: the run detail is the whole page, not a body
    // swapped inside the environment workspace.
    renderWorkspace("/dashboard/simulate/environments/job-done/runs/rt1/ex1");

    // The run detail is shown (its header names the ordinal).
    expect((await screen.findAllByText("Run 1")).length).toBeGreaterThan(0);

    // ...and the environment-workspace chrome around it is gone: no tab rail and
    // no header "Run simulation" button. The run page owns the viewport.
    expect(screen.queryByRole("tab", { name: /Contract/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Run simulation/ }),
    ).not.toBeInTheDocument();
  });

  it("shows no fixture world on a real harness env whose outputs are empty", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);

    renderWorkspace("/dashboard/simulate/environments/job-done");

    expect(await screen.findByText("Done Environment", { selector: "p" }))
      .toBeInTheDocument();
    // The "Customer Support Line" fixture world (MOCK_WORLD) used to fill every
    // missing field on a real environment, so its tools, rules and description
    // rendered as this environment's own. With the overlay gone the sections are
    // empty, not invented.
    expect(screen.queryByText(/verify_identity/)).toBeNull();
    expect(screen.queryByText(/Goodwill credit is capped/)).toBeNull();
    expect(
      screen.queryByText(/A returns-and-orders phone line for a mid-size retailer/),
    ).toBeNull();
  });

  it("renders the Scenarios tab of an empty-world harness env without crashing", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);

    // The Add-scenarios drawer derives its candidate pool from the env's tools
    // and rules the moment the tab mounts. The overlay used to guarantee those
    // arrays existed; on a real env with an empty world they are absent, so the
    // pool derivation must tolerate it rather than throw.
    renderWorkspace("/dashboard/simulate/environments/job-done?tab=scenarios");

    expect(await screen.findByText("Done Environment", { selector: "p" }))
      .toBeInTheDocument();
    // No fixture scenario content leaked in either.
    expect(screen.queryByText(/verify_identity/)).toBeNull();
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
      scenarios: [JOB_SCENARIO],
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

describe("EnvironmentWorkspace unsaved changes", () => {
  const DRAFT = {
    id: "ed",
    status: "draft",
    parent_id: "e1",
    stale: false,
    revision: 1,
    last_edited_by: { id: "u1", name: "Asha" },
    updated_at: NOW,
    parent_label: "v1",
    default_label: "v1",
    changes: { tools: 1, rules: 0, sub_goals: 0, world_files: 0 },
  };
  const VERSIONED = { ...COMPLETED_JOB, versioning: { enabled: true } };
  const conversationIn = (state, extra = {}) => ({
    conversation_id: "c-drafts",
    state,
    runtime: { available: true },
    messages: [],
    events: [],
    ...extra,
  });
  const asked = {
    message_id: "q1",
    role: "assistant",
    kind: "question",
    state: "completed",
    content: "How strict?",
    created_at: NOW,
    sequence: 1,
  };
  // The command that started the turn stays "delivered" until the turn completes.
  const command = (state) => ({
    message_id: "m0",
    role: "user",
    kind: "message",
    state,
    content: "Tighten the rules",
    created_at: NOW,
    sequence: 0,
  });
  const reply = (state) => ({
    message_id: "a1",
    role: "user",
    reply_to: "q1",
    state,
    content: "Strict",
    created_at: NOW,
    sequence: 2,
  });
  const pausedOn = (commandState, ...replies) =>
    conversationIn("waiting_for_user", {
      blocking_input: {
        message_id: "q1",
        kind: "question_requested",
        prompt: "How strict?",
      },
      messages: [command(commandState), asked, ...replies],
    });
  const questionIn = (...replies) => pausedOn("delivered", ...replies);
  const draftReads = () =>
    axios.get.mock.calls.filter(([url]) => url.endsWith("/drafts/")).length;
  const serve = (drafts) =>
    axios.get.mockImplementation((url) => {
      if (url.endsWith("/drafts/")) return Promise.resolve({ data: drafts });
      if (url.endsWith("/versions/")) {
        return Promise.resolve({
          data: { agent: [], environment: [], suite: [] },
        });
      }
      if (url.endsWith("/nudges/")) return Promise.resolve({ data: [] });
      if (/\/harness-environments\/job-done\/$/.test(url)) {
        return Promise.resolve({ data: DETAIL_NOT_AUTHORED });
      }
      return Promise.reject(
        Object.assign(new Error("Not found"), { statusCode: 404 }),
      );
    });
  const saveButton = () => screen.findByRole("button", { name: "Save" });

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    resetEnvironmentsStore();
    clearScenarioSelection();
    getHarnessJob.mockReset();
    axios.get.mockReset();
    serve({ environment: DRAFT, suite: null, set_aside: [] });
  });

  it("shows the bar right under the version bar while drafts are open", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED);
    renderWorkspace("/dashboard/simulate/environments/job-done");
    const bar = await screen.findByTestId("draft-bar");
    // The version bar ends on the scenario count.
    expect(bar.previousElementSibling).toHaveTextContent(/· \d+ scenarios?/);
    expect(await saveButton()).toBeEnabled();
  });

  it("opens the draft in the Versions tab from the bar", async () => {
    getHarnessJob.mockResolvedValue(VERSIONED);
    renderWorkspace("/dashboard/simulate/environments/job-done");
    await userEvent.click(
      await screen.findByRole("button", { name: "View changes" }),
    );
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/simulate/environments/job-done?tab=versions&version=environment%3Aed",
    );
  });

  it("shows no bar without drafts", async () => {
    serve({ environment: null, suite: null, set_aside: [] });
    getHarnessJob.mockResolvedValue(VERSIONED);
    renderWorkspace("/dashboard/simulate/environments/job-done");
    await screen.findByText(/^· \d+ scenarios?$/);
    await waitFor(() =>
      expect(
        axios.get.mock.calls.some(([url]) => url.endsWith("/drafts/")),
      ).toBe(true),
    );
    expect(screen.queryByTestId("draft-bar")).toBeNull();
  });

  it.each([
    ["the builder is working", conversationIn("responding")],
    ["an answer is queued", questionIn(reply("queued"))],
    ["an answer is delivered", questionIn(reply("delivered"))],
  ])("keeps Save off while %s", async (_, conversation) => {
    getHarnessJob.mockResolvedValue({ ...VERSIONED, conversation });
    renderWorkspace("/dashboard/simulate/environments/job-done");
    expect(
      await screen.findByText("Chat is working; save when it finishes"),
    ).toBeVisible();
    expect(await saveButton()).toBeDisabled();
  });

  it("holds Save and Discard while a rebuild is queued, and offers Stop in the composer", async () => {
    serve({ environment: DRAFT, suite: null, set_aside: [] });
    const drafts = axios.get.getMockImplementation();
    axios.get.mockImplementation((url) =>
      url.endsWith("/nudges/")
        ? Promise.resolve({
            data: [
              {
                id: "n1",
                target: "environment",
                state: "queued",
                can_stop: true,
                facts: {},
              },
            ],
          })
        : drafts(url),
    );
    getHarnessJob.mockResolvedValue({
      ...VERSIONED,
      conversation: conversationIn("warm_idle"),
    });
    renderWorkspace("/dashboard/simulate/environments/job-done");
    const reason = "An update is running; try again when it finishes";
    expect(await screen.findByText(reason)).toBeVisible();
    expect(await saveButton()).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Discard" }),
    ).toHaveAccessibleDescription(reason);
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("offers Save as soon as the builder asks a question", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: conversationIn("responding"),
      });
      renderWorkspace("/dashboard/simulate/environments/job-done");
      await waitFor(async () => expect(await saveButton()).toBeDisabled());

      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: questionIn(),
      });
      await act(() => vi.advanceTimersByTimeAsync(2000));
      expect(
        (await screen.findAllByText("How strict?")).length,
      ).toBeGreaterThan(0);
      expect(await saveButton()).toBeEnabled();
      expect(
        screen.queryByText("Chat is working; save when it finishes"),
      ).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps Save off while a turn that ends on an open question saves", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: questionIn(),
      });
      renderWorkspace("/dashboard/simulate/environments/job-done");
      await waitFor(async () => expect(await saveButton()).toBeEnabled());

      // The turn completes with its question still unanswered.
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: pausedOn("completed"),
      });
      await act(() => vi.advanceTimersByTimeAsync(2000));
      await waitFor(async () => expect(await saveButton()).toBeDisabled());
      const reads = draftReads();

      await act(() => vi.advanceTimersByTimeAsync(SAVE_GRACE_MS));
      await waitFor(async () => expect(await saveButton()).toBeEnabled());
      expect(draftReads()).toBeGreaterThan(reads);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps Save off until a finished turn's save has had time to land", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: conversationIn("responding"),
      });
      renderWorkspace("/dashboard/simulate/environments/job-done");
      await waitFor(async () => expect(await saveButton()).toBeDisabled());

      // The turn ends: chat is idle, but its save may still be landing.
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: conversationIn("idle"),
      });
      await act(() => vi.advanceTimersByTimeAsync(2000));
      await waitFor(() =>
        expect(getHarnessJob.mock.results.length).toBeGreaterThan(1),
      );
      expect(await saveButton()).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(SAVE_GRACE_MS));
      await waitFor(async () => expect(await saveButton()).toBeEnabled());
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers Save after a turn ends with its acked answer still delivered", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: conversationIn("responding", {
          messages: [command("delivered"), asked, reply("delivered")],
        }),
      });
      renderWorkspace("/dashboard/simulate/environments/job-done");
      await waitFor(async () => expect(await saveButton()).toBeDisabled());

      // The turn took the answer mid-turn, so it is never marked completed.
      getHarnessJob.mockResolvedValue({
        ...VERSIONED,
        conversation: conversationIn("warm_idle", {
          messages: [command("completed"), asked, reply("delivered")],
        }),
      });
      await act(() => vi.advanceTimersByTimeAsync(2000));
      await waitFor(() =>
        expect(getHarnessJob.mock.results.length).toBeGreaterThan(1),
      );

      await act(() => vi.advanceTimersByTimeAsync(SAVE_GRACE_MS));
      await waitFor(async () => expect(await saveButton()).toBeEnabled());
    } finally {
      vi.useRealTimers();
    }
  });
});
