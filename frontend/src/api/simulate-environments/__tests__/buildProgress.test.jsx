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
        failure: { stage: "building_environment", message: "boom", domain: "infrastructure" },
      }),
    );
    expect(out.running).toBe(false);
    expect(out.done).toEqual(["understand"]);
    expect(out.failure).toMatchObject({ stepId: "generate-env", retryable: true });
    expect(out.failure.detail).toBe("boom");
  });

  it("returns an empty slice for a job with no status", () => {
    expect(jobToBuildProgress(undefined)).toEqual({ done: [], running: false, failure: null });
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
      () => useBuildProgress({ envId: "job-1", agentRef: "acme/bot@main", enabled: true }),
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
      () => useBuildProgress({ envId: "job-1", agentRef: "acme/bot@main", enabled: false }),
      { wrapper: makeWrapper() },
    );
    expect(getHarnessJob).not.toHaveBeenCalled();
    expect(result.current.done).toEqual([]);
  });

  it("answers a console ask with a fixture reply (no build API)", () => {
    const { result } = renderHook(
      () => useBuildProgress({ envId: "job-1", agentRef: "acme/bot@main", enabled: false }),
      { wrapper: makeWrapper() },
    );
    act(() => result.current.send("what did you seed?"));
    const [userTurn, builderTurn] = result.current.turns;
    expect(userTurn).toMatchObject({ role: "user", text: "what did you seed?" });
    expect(builderTurn.steps[0]).toMatchObject({ kind: "json", label: "seeded" });
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
