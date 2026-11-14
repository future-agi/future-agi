import { act, renderHook, waitFor } from "@testing-library/react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { resetEnvironmentsStore } from "src/sections/simulate/environments/store/useEnvironmentsStore";
import { jobToBuildProgress } from "src/sections/simulate/environments/buildEnvironment/buildPipeline.constants";

vi.mock("src/api/harness/harness", () => ({ getHarnessJob: vi.fn() }));
const { getHarnessJob } = await import("src/api/harness/harness");
const { STEP_MS, useBuildProgress } = await import("../buildProgress");

const jobAt = (stage, statusExtra = {}) => ({
  status: { stage, ...statusExtra },
  events: [],
  stage_outputs: [],
});

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

// ---- The pure mapping (the important logic) --------------------------------
describe("jobToBuildProgress", () => {
  it("credits nothing while still understanding the agent", () => {
    expect(jobToBuildProgress(jobAt("understanding_agent"))).toEqual({
      done: [],
      running: true,
      failure: null,
    });
  });

  it("accrues milestones as the stage advances", () => {
    expect(jobToBuildProgress(jobAt("generating_scenarios")).done).toEqual([
      "understand",
      "build",
    ]);
  });

  it("marks every milestone done and running false at completed", () => {
    expect(jobToBuildProgress(jobAt("completed"))).toEqual({
      done: ["understand", "build", "scenarios"],
      running: false,
      failure: null,
    });
  });

  it("maps a failure stage to its milestone's first pipeline step", () => {
    const out = jobToBuildProgress(
      jobAt("failed", {
        failure: {
          stage: "building_environment",
          message: "boom",
          domain: "infrastructure",
        },
      }),
    );
    expect(out.running).toBe(false);
    expect(out.done).toEqual(["understand"]);
    expect(out.failure).toMatchObject({
      stepId: "generate-env",
      retryable: true,
    });
    // A run issue reads as one that doesn't count against the agent; "boom" stays on the record.
    expect(out.failure.detail).toBe(
      "Stopped — an issue occurred while running this. Run it again; it doesn't count against the agent.",
    );
  });

  it("reads an older build's start timeout as a slow start, not as a run issue", () => {
    // Recorded before failures carried a cause: infrastructure domain, depends_on_timeout.
    const out = jobToBuildProgress(
      jobAt("failed", {
        failure: {
          stage: "generating_environment",
          domain: "infrastructure",
          message:
            "depends_on/depends_on_timeout (agent): agent: started_check did not pass within 180.0s",
        },
      }),
    );
    expect(out.failure.detail).toBe(
      "Didn't start in time — the agent wasn't ready within the limit. Run it again; if it repeats, make it start or listen sooner.",
    );

    // An older image certifies without recording a cause: the start timeout still reads so.
    const unrecorded = jobToBuildProgress(
      jobAt("failed", {
        failure: {
          stage: "validating_environment",
          domain: "environment",
          code: "authoring_runtime_validation_failed",
          cause: "unknown",
          message:
            "We couldn't run this agent version in its environment: ProcessRuntimeError: depends_on/depends_on_timeout (agent)",
        },
      }),
    );
    expect(unrecorded.failure.detail).toBe(
      "Didn't start in time — the agent wasn't ready within the limit. Run it again; if it repeats, make it start or listen sooner.",
    );
  });

  it("says a named failure cause in plain words, never the raw guest line", () => {
    const failedWith = (cause) =>
      jobToBuildProgress(
        jobAt("failed", {
          failure: {
            stage: "validating_environment",
            domain: "environment",
            code: "authoring_runtime_validation_failed",
            cause,
            message:
              "We couldn't run this agent version in its environment: ProcessRuntimeError: depends_on/spawn_failed (agent)",
          },
        }),
      ).failure.detail;

    expect(failedWith("agent_exited_before_ready")).toBe(
      "Didn't start — the agent exited before it was ready. Fix the crash on startup, then push a new agent version.",
    );
    expect(failedWith("agent_not_ready_in_time")).toBe(
      "Didn't start in time — the agent wasn't ready within the limit. Run it again; if it repeats, make it start or listen sooner.",
    );
    // Exit 78 with no recorded cause, or one this page does not know: a next step, no code,
    // and never "run it again" for a deterministic failure.
    for (const cause of ["unknown", "a_value_from_a_newer_server"]) {
      expect(failedWith(cause)).toBe(
        "The agent stopped before it was ready and didn't say why. Start it on your own machine to see the error it prints.",
      );
    }
  });

  it("leaves a failure with no cause outside our infrastructure as recorded", () => {
    const out = jobToBuildProgress(
      jobAt("failed", {
        failure: {
          stage: "building_environment",
          domain: "environment",
          code: "provider_credentials_invalid",
          message: "The Vapi key was rejected.",
        },
      }),
    );
    expect(out.failure.detail).toBe("The Vapi key was rejected.");
  });

  it("marks a canceled build terminal (freezes the animation) without failing a step", () => {
    const out = jobToBuildProgress(jobAt("canceled"));
    expect(out.running).toBe(false);
    // A cancel is terminal, so it carries a failure marker — that is what stops
    // DerivingAnimation. But no specific step failed, so stepId is null and the
    // pipeline just stops where it was rather than showing a red step.
    expect(out.failure).toMatchObject({ canceled: true, retryable: false });
    expect(out.failure.stepId).toBeNull();
  });

  it("returns an empty slice for a job with no status", () => {
    expect(jobToBuildProgress(undefined)).toEqual({
      done: [],
      running: false,
      failure: null,
    });
  });
});

// ---- Real poll path --------------------------------------------------------
describe("useBuildProgress — real poll", () => {
  beforeEach(() => {
    resetEnvironmentsStore();
    getHarnessJob.mockReset();
  });

  it("derives the slice from the live job and narrates milestones", async () => {
    getHarnessJob.mockResolvedValue(jobAt("completed"));
    const { result } = renderHook(
      () =>
        useBuildProgress({
          envId: "job-1",
          agentRef: "acme/bot@main",
          enabled: true,
        }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() =>
      expect(result.current.done).toEqual(["understand", "build", "scenarios"]),
    );
    expect(result.current.running).toBe(false);
    expect(result.current.turns.length).toBeGreaterThan(0);
  });

  it("does not fetch while disabled", () => {
    getHarnessJob.mockResolvedValue(jobAt("completed"));
    const { result } = renderHook(
      () =>
        useBuildProgress({
          envId: "job-1",
          agentRef: "acme/bot@main",
          enabled: false,
        }),
      { wrapper: makeWrapper() },
    );
    expect(getHarnessJob).not.toHaveBeenCalled();
    expect(result.current.done).toEqual([]);
  });

  it("answers a console ask with a fixture reply (no build API)", () => {
    const { result } = renderHook(
      () =>
        useBuildProgress({
          envId: "job-1",
          agentRef: "acme/bot@main",
          enabled: false,
        }),
      { wrapper: makeWrapper() },
    );
    act(() => result.current.send("what did you seed?"));
    const [userTurn, builderTurn] = result.current.turns;
    expect(userTurn).toMatchObject({
      role: "user",
      text: "what did you seed?",
    });
    expect(builderTurn.steps[0]).toMatchObject({
      kind: "json",
      label: "seeded",
    });
  });
});

// ---- Mock timer walk (mockMode: a draft that could not be built for real) --
describe("useBuildProgress — mockMode timer walk", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetEnvironmentsStore();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("walks the fixture stages to completion", () => {
    const { result } = renderHook(
      () =>
        useBuildProgress({
          envId: "env-1",
          agentRef: "acme/support-bot@main",
          enabled: true,
          mockMode: true,
        }),
      { wrapper: makeWrapper() },
    );
    act(() => vi.advanceTimersByTime(20000));
    expect(result.current.done).toEqual(["understand", "build", "scenarios"]);
    expect(result.current.running).toBe(false);
    expect(getHarnessJob).not.toHaveBeenCalled();
  });

  it("resets the walk when enabled flips back to false mid-run", () => {
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useBuildProgress({
          envId: "env-1",
          agentRef: "acme/support-bot@main",
          enabled,
          mockMode: true,
        }),
      { wrapper: makeWrapper(), initialProps: { enabled: true } },
    );
    act(() => vi.advanceTimersByTime(STEP_MS * 3));
    expect(result.current.turns).toHaveLength(1);

    // The walk's own state resets: timers cleared, reducer reset.
    rerender({ enabled: false });
    expect(result.current.turns).toEqual([]);
    expect(result.current.running).toBe(false);
  });
});
