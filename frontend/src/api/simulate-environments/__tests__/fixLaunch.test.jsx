import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const axios = (await import("src/utils/axios")).default;
const { resetFixRunFixtures, fixtureFixRuns } = await import(
  "./fixtures/fixRuns"
);
const { useFixLaunchOptions, useLaunchFix } = await import("../fixRuns");

const makeWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("launch options", () => {
  beforeEach(() => {
    axios.get.mockClear();
    resetFixRunFixtures();
  });

  it("returns the contract's launch-options shape for a run", async () => {
    const { result } = renderHook(() => useFixLaunchOptions("te-run-6"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.options).toBeDefined());
    const o = result.current.options;
    expect(o.source_run).toMatchObject({ id: "te-run-6", state: "completed" });
    expect(Object.keys(o).sort()).toEqual(
      [
        "agent",
        "blocked",
        "defaults",
        "environment",
        "limits",
        "scenarios",
        "source_run",
        "suggested",
        "surfaces",
      ].sort(),
    );
    expect(o.limits).toEqual({
      budget_credits_max: 50000,
      max_minutes_max: 1440,
      scenarios_max: 200,
    });
    expect(o.defaults).toEqual({
      surfaces: ["prompt"],
      budget_credits: 1500,
      max_minutes: 150,
    });
    expect(o.blocked).toBeNull();
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/agent-fix-runs/launch-options/",
      { params: { source_run_id: "te-run-6" } },
    );
  });

  it("suggests every failing scenario to fix and every passing one to protect", async () => {
    const { result } = renderHook(() => useFixLaunchOptions("te-run-6"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.options).toBeDefined());
    const { scenarios, suggested } = result.current.options;
    const failing = scenarios
      .filter((s) => s.failing_sub_goals.length)
      .map((s) => s.key);
    const passing = scenarios
      .filter((s) => s.passed === true)
      .map((s) => s.key);
    expect(suggested.target_scenario_keys).toEqual(failing);
    expect(suggested.protected_scenario_keys).toEqual(passing);
    expect(failing.length).toBeGreaterThanOrEqual(12);
    expect(scenarios.some((s) => s.passed === null)).toBe(true);
  });
});

describe("launching a fix", () => {
  beforeEach(() => resetFixRunFixtures());

  it("creates a queued fix in the environment's list", async () => {
    const { result } = renderHook(() => useLaunchFix("env-1"), {
      wrapper: makeWrapper(),
    });
    const targets = Array.from({ length: 12 }, (_, i) => `t${i}`);
    let fix;
    await act(async () => {
      fix = await result.current.mutateAsync({
        source_run_id: "te-run-6",
        target_scenario_keys: targets,
        protected_scenario_keys: ["p1"],
        surfaces: ["prompt", "tools"],
        locked_paths: [],
        budget_credits: 1500,
        max_minutes: 60,
      });
    });
    expect(fix.state).toBe("queued");
    expect(fix.source_run_id).toBe("te-run-6");
    expect(fix.request).toMatchObject({
      target_scenario_keys: targets,
      protected_scenario_keys: ["p1"],
      surfaces: ["prompt", "tools"],
      budget_credits: 1500,
      max_minutes: 60,
    });
    expect(fix.request.held_back_scenario_keys).toHaveLength(4);
    // The backend refuses unknown fields, so the body is the contract's only.
    const [url, body] = axios.post.mock.calls.at(-1);
    expect(url).toBe("/simulate/api/agent-fix-runs/");
    expect(Object.keys(body).sort()).toEqual(
      [
        "budget_credits",
        "locked_paths",
        "max_minutes",
        "protected_scenario_keys",
        "source_run_id",
        "surfaces",
        "target_scenario_keys",
      ].sort(),
    );
    expect(fixtureFixRuns("env-1")[0].id).toBe(fix.id);
  });

  it("holds nothing back below 12 picked", async () => {
    const { result } = renderHook(() => useLaunchFix("env-1"), {
      wrapper: makeWrapper(),
    });
    let fix;
    await act(async () => {
      fix = await result.current.mutateAsync({
        source_run_id: "te-run-6",
        target_scenario_keys: ["a", "b", "c"],
        budget_credits: 1500,
      });
    });
    expect(fix.request.held_back_scenario_keys).toEqual([]);
  });
});
