import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ENV_STATUS } from "src/sections/simulate/environments/myEnvironments.constants";
import { environmentName } from "src/pages/dashboard/harness/harnessShared";

vi.mock("src/api/harness/harness", () => ({ getHarnessJob: vi.fn() }));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  getHarnessEnvironment: vi.fn(),
}));

const { getHarnessJob } = await import("src/api/harness/harness");
const { getHarnessEnvironment } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const {
  useEnvironment,
  harnessJobToEnvironment,
  stageOutputsToWorld,
  harnessEnvState,
  canRunHeader,
} = await import("../environment");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "src/sections/simulate/environments/store/useEnvironmentsStore"
);
const { MOCK_WORLD } = await import("../_fixtures/world");

// A completed harness job detail. `platform` sits at the TOP LEVEL, sibling of
// `job`/`status`/`stage_outputs` — `status` deliberately carries no platform, so
// a mapping that read `status.platform` would surface undefined and fail.
const COMPLETED_JOB = {
  job: {
    job_id: "job-done",
    metadata: { name: "Done Environment" },
    scenario_count: 5,
    agent: { connector: "livekit" },
  },
  status: { stage: "completed", updated_at: "2026-09-15T09:00:00Z" },
  credentials: { detected_connectors: ["livekit"] },
  platform: { run_test_id: "rt1", test_execution_id: "ex1" },
  stage_outputs: [
    {
      id: "o1",
      kind: "contract",
      data: {
        one_liner: "A returns-and-orders phone line.",
        modality: "voice",
        tools: [{ name: "lookup_order" }, { name: "issue_refund" }],
        hard_constraints: ["Verify identity before changing an account."],
      },
    },
    { id: "o2", kind: "environment", data: { services: ["postgres", "redis"] } },
  ],
};

// A job whose calls are running: "running" is stage 10 of 14, past
// connecting_agent, so the environment itself is already built.
const RUNNING_JOB = {
  job: {
    job_id: "job-run",
    metadata: { name: "Running Environment" },
    scenario_count: 5,
    agent: { connector: "livekit" },
  },
  status: { stage: "running", updated_at: "2026-09-15T09:00:00Z" },
  credentials: { detected_connectors: ["livekit"] },
  stage_outputs: [],
};

// A job still assembling its world — the stage the build experience renders on.
const BUILDING_JOB = {
  ...RUNNING_JOB,
  job: { ...RUNNING_JOB.job, job_id: "job-building", metadata: { name: "Building Environment" } },
  status: { stage: "generating_environment", updated_at: "2026-09-15T09:00:00Z" },
};

// A completed job that also carries top-level registered scenarios, to exercise
// the job.scenarios[] fallback in harnessEnvState.
const JOB_WITH_SCENARIOS = {
  job: { job_id: "job-sc", metadata: { name: "Scenario Env" }, scenario_count: 9 },
  status: { stage: "completed", updated_at: "2026-09-15T09:00:00Z" },
  credentials: { detected_connectors: ["vapi"] },
  scenarios: [
    { scenario_key: "sk1", name: "sc_one", instruction: "Track a delivery", use_case: "UC1" },
    { scenario_key: "sk2", name: "sc_two", instruction: "Dispute a charge", use_case: "UC2" },
  ],
  stage_outputs: [],
};

const notFoundError = () => {
  // The axios interceptor rejects with statusCode (not response.status).
  const err = new Error("job not found");
  err.statusCode = 404;
  return err;
};

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return { queryClient, Wrapper };
};
beforeEach(() => {
  resetEnvironmentsStore();
  getHarnessJob.mockReset();
  getHarnessEnvironment.mockReset();
  getHarnessEnvironment.mockResolvedValue(null);
});

describe("useEnvironment resolution order", () => {
  it("returns a client env from the store without fetching", async () => {
    useEnvironmentsStore
      .getState()
      .adoptEnvironment({ id: "env-1", name: "My Build" }, Date.now());
    const { Wrapper } = makeWrapper();

    const { result } = renderHook(() => useEnvironment("env-1"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("client"));
    expect(result.current.env.id).toBe("env-1");
    expect(getHarnessJob).not.toHaveBeenCalled();
  });

  it("fetches the harness job on a store miss and maps name/status", async () => {
    getHarnessJob.mockResolvedValue(RUNNING_JOB);
    const { Wrapper } = makeWrapper();

    const { result } = renderHook(() => useEnvironment("job-run"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("harness"));
    expect(getHarnessJob).toHaveBeenCalledTimes(1);
    expect(result.current.env.name).toBe(environmentName(RUNNING_JOB.job));
    expect(result.current.env.status).toBe(ENV_STATUS.RUNNING);
  });

  it("reads platform ids from the top-level item.platform", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const { Wrapper } = makeWrapper();

    const { result } = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("harness"));
    expect(result.current.env.platform.runTestId).toBe("rt1");
    expect(result.current.env.platform.testExecutionId).toBe("ex1");
  });

  it("polls while non-terminal and stops once completed", async () => {
    const { queryClient, Wrapper } = makeWrapper();

    getHarnessJob.mockResolvedValue(RUNNING_JOB);
    const running = renderHook(() => useEnvironment("job-run"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(running.result.current.source).toBe("harness"));
    const runningQuery = queryClient
      .getQueryCache()
      .find({ queryKey: ["harness-job", "job-run"] });
    expect(runningQuery.options.refetchInterval(runningQuery)).toBeTruthy();

    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const done = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(done.result.current.source).toBe("harness"));
    const doneQuery = queryClient
      .getQueryCache()
      .find({ queryKey: ["harness-job", "job-done"] });
    expect(doneQuery.options.refetchInterval(doneQuery)).toBe(false);
  });

  it("takes world tools from the contract output, and carries none without one", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const { Wrapper } = makeWrapper();
    const parsed = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(parsed.result.current.source).toBe("harness"));
    expect(parsed.result.current.env.tools.map((t) => t.name)).toEqual([
      "lookup_order",
      "issue_refund",
    ]);

    // RUNNING_JOB carries no parseable stage output. A real environment must not
    // be filled in from the "Customer Support Line" fixture world.
    getHarnessJob.mockResolvedValue(RUNNING_JOB);
    const unparseable = renderHook(() => useEnvironment("job-run"), {
      wrapper: Wrapper,
    });
    await waitFor(() =>
      expect(unparseable.result.current.source).toBe("harness"),
    );
    const env = unparseable.result.current.env;
    expect(env.tools).toBeUndefined();
    expect(env.rules).toBeUndefined();
    expect(env.seed).toBeUndefined();
    expect(env.description).toBeUndefined();
    expect(env.name).not.toBe(MOCK_WORLD.name);
  });

  it("bootstraps an endpoint agent and no scenarios until the run emits them", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.bootstrapState).toBeDefined());
    expect(result.current.bootstrapState.agent.via).toBe("endpoint");
    // COMPLETED_JOB declares scenario_count but emits no scenarios output and
    // registers none; the derived fixture pool must not stand in for scenarios
    // the run never made.
    expect(result.current.bootstrapState.scenarios).toEqual([]);
  });

  it("enables canRunHeader only when a completed harness has real scenarios", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("harness"));
    expect(canRunHeader("harness", result.current.env, true)).toBe(true);
    expect(canRunHeader("harness", result.current.env, false)).toBe(false);
  });

  it("flags an unknown id as notFound on a 404", async () => {
    getHarnessJob.mockRejectedValue(notFoundError());
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("nope"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.notFound).toBe(true));
    expect(result.current.env).toBeNull();
  });

  it("resolves a template id from the prebuilt catalogue", async () => {
    getHarnessJob.mockRejectedValue(notFoundError());
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("env-voice-support"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("template"));
    expect(result.current.env.id).toBe("env-voice-support");
  });
});

describe("stageOutputsToWorld", () => {
  it("parses a contract output into world fields", () => {
    const world = stageOutputsToWorld(COMPLETED_JOB.stage_outputs);
    expect(world.tools.map((t) => t.name)).toEqual([
      "lookup_order",
      "issue_refund",
    ]);
    expect(world.rules).toEqual([
      "Verify identity before changing an account.",
    ]);
    // environment.services surface; the output carries no seed tables.
    expect(world.seed.services).toEqual(["postgres", "redis"]);
    expect(world.seed.tables).toEqual([]);
  });

  it("returns null when nothing is parseable", () => {
    expect(stageOutputsToWorld([])).toBeNull();
    expect(stageOutputsToWorld([{ kind: "runs", data: {} }])).toBeNull();
  });

  it("maps scenario output rows into table-ready scenarios", () => {
    const outputs = [
      {
        kind: "scenarios",
        data: [
          { name: "happy_path", instruction: "Answer a delivery query.", use_case: "Track a delivery" },
        ],
      },
    ];
    const state = harnessEnvState(
      { job: { job_id: "j", scenario_count: 1 }, status: {} },
      stageOutputsToWorld(outputs),
    );
    expect(state.scenarios[0]).toMatchObject({
      id: "happy_path",
      name: "happy_path",
      task: "Answer a delivery query.",
      useCase: "Track a delivery",
    });
  });

  it("falls back to the job's registered scenarios when outputs carry none", () => {
    const { world } = harnessJobToEnvironment(JOB_WITH_SCENARIOS);
    const state = harnessEnvState(JOB_WITH_SCENARIOS, world);
    // Neither stage_outputs scenarios nor a fixture pool — the two registered
    // scenarios win.
    expect(state.scenarios.map((s) => s.id)).toEqual(["sk1", "sk2"]);
  });
});

describe("harnessJobToEnvironment — only the real world, no fixture fill", () => {
  it("surfaces parsed world fields and carries the real seed verbatim", () => {
    const { env } = harnessJobToEnvironment(COMPLETED_JOB);
    expect(env.tools.map((t) => t.name)).toEqual(["lookup_order", "issue_refund"]);
    expect(env.seed.services).toEqual(["postgres", "redis"]);
    // The run emitted no seed tables, so none are shown — the "Customer Support
    // Line" fixture tables must not fill the gap.
    expect(env.seed.tables).toEqual([]);
    // No provenance is produced: without the overlay every present field is real,
    // and an absent one renders its own empty state rather than a Sample badge.
    expect(env.provenance).toBeUndefined();
  });

  it("leaves every world field absent when the run parses nothing", () => {
    const { env } = harnessJobToEnvironment(RUNNING_JOB);
    expect(env.tools).toBeUndefined();
    expect(env.rules).toBeUndefined();
    expect(env.seed).toBeUndefined();
    expect(env.description).toBeUndefined();
    expect(env.evalPreset).toBeUndefined();
    expect(env.name).not.toBe(MOCK_WORLD.name);
  });
});

describe("harnessJobToEnvironment", () => {
  it("reads platform from the top-level detail response", () => {
    const { env } = harnessJobToEnvironment(COMPLETED_JOB);
    expect(env.platform.runTestId).toBe("rt1");
    expect(env.platform.testExecutionId).toBe("ex1");
    expect(env.status).toBe(ENV_STATUS.COMPLETED);
  });

  it("exposes buildProgress as { done, total } so the building banner can count", () => {
    const { env } = harnessJobToEnvironment(BUILDING_JOB);
    expect(env.buildStatus).toBe("building");
    expect(env.buildProgress).toEqual({
      done: expect.any(Number),
      total: expect.any(Number),
    });
    expect(env.buildProgress.total).toBeGreaterThan(0);
  });

  // "running" is past connecting_agent in the pipeline, so the world is derived
  // and the build experience must give way to the workspace.
  it("reads a running job as a built environment, not one still building", () => {
    expect(harnessJobToEnvironment(RUNNING_JOB).env.buildStatus).toBe("ready");
  });

  it("marks a terminal-failed job as failed (not building) and carries the failure", () => {
    const failed = {
      ...RUNNING_JOB,
      status: {
        stage: "failed",
        updated_at: "2026-09-21T14:02:13Z",
        failure: { domain: "infrastructure", stage: "queued", code: "sandbox_launch_failed", message: "boom" },
      },
    };
    const { env } = harnessJobToEnvironment(failed);
    expect(env.buildStatus).toBe("failed");
    expect(env.status).toBe(ENV_STATUS.FAILED);
    expect(env.buildError).toMatchObject({ code: "sandbox_launch_failed", message: "boom" });
  });

  it("marks a canceled job as failed too", () => {
    const canceled = { ...RUNNING_JOB, status: { stage: "canceled", updated_at: "2026-09-21T14:02:13Z" } };
    expect(harnessJobToEnvironment(canceled).env.buildStatus).toBe("failed");
  });
});

describe("canRunHeader", () => {
  it("requires both a persisted RunTest and real runnable scenarios", () => {
    const completed = { status: "completed", platform: { runTestId: "rt1" } };
    expect(canRunHeader("harness", completed, true)).toBe(true);
    expect(canRunHeader("harness", completed, false)).toBe(false);
    expect(canRunHeader("harness", { status: "completed", platform: {} }, true)).toBe(false);
    expect(canRunHeader("harness", { ...completed, status: "finalizing" }, true)).toBe(false);
  });

  it("falls back to canRun for non-harness sources", () => {
    expect(canRunHeader("client", {}, true)).toBe(true);
    expect(canRunHeader("client", { platform: { runTestId: "rt1" } }, false)).toBe(false);
  });
});

describe("useEnvironment status when the detail is older than the job poll", () => {
  const DETAIL_FROM_VALIDATION = {
    id: "job-fin",
    overview: {
      id: "job-fin",
      name: "Finalizing Environment",
      agent_type: "voice",
      status: "building",
      stage: "validating_scenarios",
    },
    contract: { one_liner: "A returns-and-orders phone line." },
  };
  const jobInCleanup = (status) => ({
    ...RUNNING_JOB,
    job: { ...RUNNING_JOB.job, job_id: "job-fin" },
    status: { stage: "cleaning_up", updated_at: "2026-09-15T09:05:00Z", ...status },
    platform: { run_test_id: "rt1", test_execution_id: "ex1" },
  });

  const renderEnv = async (job) => {
    getHarnessJob.mockResolvedValue(job);
    getHarnessEnvironment.mockResolvedValue(DETAIL_FROM_VALIDATION);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("job-fin"), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.env?.description).toBe("A returns-and-orders phone line."));
    return result.current.env;
  };

  it("shows Finalizing from the job poll, not the detail's stale Building, and keeps Run disabled", async () => {
    const env = await renderEnv(jobInCleanup());
    expect(env.status).toBe(ENV_STATUS.FINALIZING);
    expect(canRunHeader("harness", env, true)).toBe(false);
  });

  it("shows Cancelling when the job poll carries a requested cancel", async () => {
    const env = await renderEnv(jobInCleanup({ cancel_requested_at: "2026-09-15T09:04:00Z" }));
    expect(env.status).toBe(ENV_STATUS.CANCELLING);
    expect(canRunHeader("harness", env, true)).toBe(false);
  });
});
