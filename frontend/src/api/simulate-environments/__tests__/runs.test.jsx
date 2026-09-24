import { describe, it, expect, beforeEach, vi } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

// Mock only the axios default instance; keep the real `endpoints` so the
// URL assertions below are genuine, not a tautology against our own mock.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { paths } = await import("src/routes/paths");
const { MOCK_RUNS } = await import("../_fixtures/runs");
const {
  useEnvironmentRuns,
  executionToRun,
  runSimulationTarget,
  mockRunsEnabled,
  RUNS_PAGE_SIZE,
} = await import("../runs");

// Raw executions payload (the product's `results[]` shape) — capitalised
// product statuses, `success_rate` on the 0–100 scale. RunTestExecutionsView
// orders by -created_at, so the payload arrives newest-first and the hook keeps
// that order.
const rawExecutions = () => ({
  results: [
    {
      id: "ex-new",
      status: "Completed",
      start_time: "2026-01-14T09:12:00.000Z",
      agent_version: "v2",
      total_chats: 12,
      success_rate: 100,
    },
    {
      id: "ex-mid",
      status: "Running",
      start_time: "2026-01-13T16:40:00.000Z",
      agent_version: "v2",
      total_chats: 12,
      success_rate: 50,
    },
    {
      id: "ex-old",
      status: "Failed",
      start_time: "2026-01-12T11:05:00.000Z",
      agent_version: "v1",
      total_chats: 10,
      success_rate: 70,
    },
  ],
  count: 3,
});

// A history longer than one page: `count` is 12 while a page carries 10.
const pageOf = (ids, count) => ({
  results: ids.map((id, i) => ({
    id,
    status: "Completed",
    start_time: `2026-01-${String(28 - i).padStart(2, "0")}T09:00:00.000Z`,
    total_chats: 4,
    success_rate: 100,
  })),
  count,
});

const makeWrapper = (initialEntries = ["/"]) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

beforeEach(() => {
  axios.get.mockReset();
  axios.get.mockResolvedValue({ data: rawExecutions() });
});

describe("executionToRun", () => {
  it("maps a completed, fully-passing execution to `passed`", () => {
    const run = executionToRun({
      id: "ex-1",
      status: "Completed",
      start_time: "2026-01-14T09:12:00.000Z",
      agent_version: "v2",
      total_chats: 12,
      success_rate: 100,
    });
    expect(run.id).toBe("ex-1");
    expect(run.executionId).toBe("ex-1");
    expect(run.status).toBe("passed");
    expect(run.total).toBe(12);
    expect(run.passed).toBe(12);
    expect(run.failed).toBe(0);
    expect(run.agentVersion).toBe("v2");
    expect(run.startedAt).toBe("2026-01-14T09:12:00.000Z");
  });

  it("maps a completed execution with failures to `failed`", () => {
    const run = executionToRun({
      id: "ex-2",
      status: "Completed",
      total_chats: 10,
      success_rate: 70,
    });
    expect(run.status).toBe("failed");
    expect(run.passed).toBe(7);
    expect(run.failed).toBe(3);
  });

  it("maps a non-terminal execution to `running`", () => {
    const run = executionToRun({ id: "ex-3", status: "Running", total_chats: 12, success_rate: 50 });
    expect(run.status).toBe("running");
  });

  it("maps an explicitly failed execution to `failed`", () => {
    const run = executionToRun({ id: "ex-4", status: "Failed", total_chats: 5, success_rate: 100 });
    expect(run.status).toBe("failed");
  });

  it("falls back to `calls_attempted` for voice executions", () => {
    const run = executionToRun({ id: "ex-5", status: "Completed", calls_attempted: 8, success_rate: 100 });
    expect(run.total).toBe(8);
  });
});

describe("useEnvironmentRuns", () => {
  it("fetches executions when the env carries platform.runTestId, newest first", async () => {
    const env = { id: "env-1", platform: { runTestId: "rt1", testExecutionId: "ex1" } };
    const { result } = renderHook(() => useEnvironmentRuns(env, { runs: [] }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.runs.length).toBe(3));

    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.runTests.detailExecutions("rt1"),
      { params: { page: 1, limit: RUNS_PAGE_SIZE } },
    );

    const [first, second, third] = result.current.runs;
    expect(first.id).toBe("ex-new");
    expect(second.id).toBe("ex-mid");
    expect(third.id).toBe("ex-old");
    expect(first.status).toBe("passed");
    expect(second.status).toBe("running");
    expect(third.status).toBe("failed");
    // Newest → highest ordinal label.
    expect(first.label).toBe("Run 3");
    expect(third.label).toBe("Run 1");
  });

  it("exposes the total and a next page when the history is longer than one page", async () => {
    const env = { id: "env-1", platform: { runTestId: "rt1" } };
    const first = pageOf(
      Array.from({ length: 10 }, (_, i) => `ex-${12 - i}`),
      12,
    );
    const second = pageOf(["ex-2", "ex-1"], 12);
    axios.get.mockReset();
    axios.get
      .mockResolvedValueOnce({ data: first })
      .mockResolvedValueOnce({ data: second });

    const { result } = renderHook(() => useEnvironmentRuns(env, { runs: [] }), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.runs.length).toBe(10));
    expect(result.current.total).toBe(12);
    expect(result.current.hasMore).toBe(true);
    // Ordinals come from the full count, not from the rows on screen.
    expect(result.current.runs[0].label).toBe("Run 12");
    expect(result.current.runs[9].label).toBe("Run 3");

    result.current.fetchMore();

    await waitFor(() => expect(result.current.runs.length).toBe(12));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.runs[11].label).toBe("Run 1");
    expect(axios.get).toHaveBeenNthCalledWith(
      2,
      endpoints.runTests.detailExecutions("rt1"),
      { params: { page: 2, limit: RUNS_PAGE_SIZE } },
    );
  });

  it("keeps polling while a loaded execution is still running", async () => {
    const env = { id: "env-1", platform: { runTestId: "rt1" } };
    const { result } = renderHook(() => useEnvironmentRuns(env, { runs: [] }), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.runs.length).toBe(3));
    expect(result.current.isPolling).toBe(true);
  });

  it("stops polling once every loaded execution is terminal", async () => {
    const env = { id: "env-1", platform: { runTestId: "rt1" } };
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: pageOf(["ex-2", "ex-1"], 2) });
    const { result } = renderHook(() => useEnvironmentRuns(env, { runs: [] }), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.runs.length).toBe(2));
    expect(result.current.isPolling).toBe(false);
  });

  it("returns envState.runs with no fetch when the env has no platform", () => {
    const envState = { runs: [{ id: "seeded", label: "Seeded" }] };
    const { result } = renderHook(
      () => useEnvironmentRuns({ id: "env-2" }, envState),
      { wrapper: makeWrapper() },
    );

    expect(axios.get).not.toHaveBeenCalled();
    expect(result.current.runs).toEqual(envState.runs);
    expect(result.current.isLoading).toBe(false);
  });

  it("renders MOCK_RUNS behind ?mockRuns=1 without fetching", () => {
    const env = { id: "env-3", platform: { runTestId: "rt1", testExecutionId: "ex1" } };
    const { result } = renderHook(() => useEnvironmentRuns(env, { runs: [] }), {
      wrapper: makeWrapper(["/?mockRuns=1"]),
    });

    expect(axios.get).not.toHaveBeenCalled();
    expect(result.current.runs).toEqual(MOCK_RUNS);
  });
});

describe("mockRunsEnabled", () => {
  it("honours ?mockRuns=1 in a dev build", () => {
    expect(mockRunsEnabled("1", true)).toBe(true);
  });

  it("ignores ?mockRuns=1 in a production build", () => {
    // The QA switch swaps real executions for a fixture; it must not be
    // reachable from a deployed build.
    expect(mockRunsEnabled("1", false)).toBe(false);
  });

  it("is off without the param", () => {
    expect(mockRunsEnabled(null, true)).toBe(false);
    expect(mockRunsEnabled("0", true)).toBe(false);
  });
});

describe("runSimulationTarget", () => {
  it("routes to the product execution detail when platform ids are present", () => {
    const env = { platform: { runTestId: "rt1", testExecutionId: "ex1" } };
    expect(runSimulationTarget(env)).toBe(
      paths.dashboard.simulate.testCallDetails("rt1", "ex1"),
    );
  });

  it("routes to the product Run Simulation entry when platform ids are absent", () => {
    expect(runSimulationTarget({})).toBe(paths.dashboard.simulate.test);
    expect(runSimulationTarget(null)).toBe(paths.dashboard.simulate.test);
  });
});
