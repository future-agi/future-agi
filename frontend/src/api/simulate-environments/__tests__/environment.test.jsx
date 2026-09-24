import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ENV_STATUS } from "src/sections/simulate/environments/myEnvironments.constants";
import { environmentName } from "src/pages/dashboard/harness/harnessShared";

vi.mock("src/api/harness/harness", () => ({ getHarnessJob: vi.fn() }));

const { getHarnessJob } = await import("src/api/harness/harness");
const {
  useEnvironment,
  harnessJobToEnvironment,
  stageOutputsToWorld,
  harnessEnvState,
  canRunHeader,
  jobRefetchInterval,
} = await import("../environment");
const { MOCK_WORLD } = await import("../_fixtures/world");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "src/sections/simulate/environments/store/useEnvironmentsStore"
);

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
  status: { stage: "completed", created_at: "2026-09-15T09:00:00Z" },
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
  ],
};

const RUNNING_JOB = {
  job: {
    job_id: "job-run",
    metadata: { name: "Building Environment" },
    scenario_count: 5,
    agent: { connector: "livekit" },
  },
  status: { stage: "running", created_at: "2026-09-15T09:00:00Z" },
  credentials: { detected_connectors: ["livekit"] },
  stage_outputs: [],
};

// The app's axios interceptor rejects with a customError carrying the HTTP
// status at `statusCode` (utils/axios.js) — `response` is not present on the
// rejected value, so a mock that sets `response.status` tests a shape production
// never produces.
const notFoundError = () => {
  const err = new Error("Not found");
  err.statusCode = 404;
  return err;
};

const serverError = () => {
  const err = new Error("Boom");
  err.statusCode = 500;
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

    // RUNNING_JOB has no parseable stage output. A real environment must not be
    // filled in from the "Customer Support Line" fixture world.
    getHarnessJob.mockResolvedValue(RUNNING_JOB);
    const unparseable = renderHook(() => useEnvironment("job-run"), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(unparseable.result.current.source).toBe("harness"));
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

    await waitFor(() => expect(result.current.source).toBe("harness"));
    expect(result.current.bootstrapState.agent.via).toBe("endpoint");
    // The job declares scenario_count 3 but emits no scenarios output; the
    // derived fixture pool must not stand in for scenarios the run never made.
    expect(result.current.bootstrapState.scenarios).toEqual([]);
  });

  it("enables canRunHeader for a completed harness job", async () => {
    getHarnessJob.mockResolvedValue(COMPLETED_JOB);
    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useEnvironment("job-done"), {
      wrapper: Wrapper,
    });

    await waitFor(() => expect(result.current.source).toBe("harness"));
    expect(canRunHeader("harness", result.current.env, false)).toBe(true);
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
});

describe("harnessJobToEnvironment", () => {
  it("reads platform from the top-level detail response", () => {
    const { env } = harnessJobToEnvironment(COMPLETED_JOB);
    expect(env.platform.runTestId).toBe("rt1");
    expect(env.platform.testExecutionId).toBe("ex1");
    expect(env.status).toBe(ENV_STATUS.COMPLETED);
  });

  it("marks a terminally failed job as failed, not still building", () => {
    const failed = {
      ...RUNNING_JOB,
      status: { ...RUNNING_JOB.status, stage: "failed" },
    };
    expect(harnessJobToEnvironment(failed).env.buildStatus).toBe("failed");
    const canceled = {
      ...RUNNING_JOB,
      status: { ...RUNNING_JOB.status, stage: "canceled" },
    };
    expect(harnessJobToEnvironment(canceled).env.buildStatus).toBe("failed");
  });

  it("exposes buildProgress as { done, total } so the building banner can count", () => {
    const { env } = harnessJobToEnvironment(RUNNING_JOB);
    expect(env.buildStatus).toBe("building");
    expect(env.buildProgress).toEqual({
      done: expect.any(Number),
      total: expect.any(Number),
    });
    expect(env.buildProgress.total).toBeGreaterThan(0);
  });
});

describe("canRunHeader", () => {
  it("gates a harness env on its platform run id", () => {
    expect(canRunHeader("harness", { platform: { runTestId: "rt1" } }, false)).toBe(true);
    expect(canRunHeader("harness", { platform: {} }, false)).toBe(false);
    expect(canRunHeader("harness", { platform: {} }, true)).toBe(true);
  });

  it("falls back to canRun for non-harness sources", () => {
    expect(canRunHeader("client", {}, true)).toBe(true);
    expect(canRunHeader("client", { platform: { runTestId: "rt1" } }, false)).toBe(false);
  });
});

describe("jobRefetchInterval", () => {
  it("keeps polling a job that has not reached a terminal stage", () => {
    expect(jobRefetchInterval({ state: { data: { status: { stage: "running" } } } })).toBe(2000);
  });

  it("stops polling once the job reaches a terminal stage", () => {
    expect(jobRefetchInterval({ state: { data: { status: { stage: "completed" } } } })).toBe(false);
  });

  it("stops polling once the query has errored", () => {
    expect(jobRefetchInterval({ state: { data: undefined, error: notFoundError() } })).toBe(false);
    expect(jobRefetchInterval({ state: { data: undefined, error: serverError() } })).toBe(false);
  });
});
