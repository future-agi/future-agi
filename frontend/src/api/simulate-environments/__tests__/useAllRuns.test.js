import { describe, it, expect, beforeEach, vi } from "vitest";
import { createElement } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { MOCK_RUNS } = await import("../_fixtures/runs");
const { useAllRuns } = await import("../useAllRuns");

const env = { id: "env-1", platform: { runTestId: "rt-1" } };

// A run-test of `count` executions, served newest-first in pages of `limit`.
function serveExecutions(count) {
  axios.get.mockImplementation((url, config) => {
    if (url !== endpoints.runTests.detailExecutions("rt-1")) {
      return Promise.resolve({ data: {} });
    }
    const { page, limit } = config.params;
    const from = (page - 1) * limit;
    const results = Array.from(
      { length: Math.max(0, Math.min(limit, count - from)) },
      (_, i) => ({
        id: `ex-${count - from - i}`,
        status: "Completed",
        total_calls: 10,
        success_rate: 80,
        completed_at: "2026-01-14T09:12:00.000Z",
      }),
    );
    return Promise.resolve({ data: { results, count } });
  });
}

const wrapper = ({ children }) =>
  createElement(
    QueryClientProvider,
    {
      client: new QueryClient({
        defaultOptions: { queries: { retry: false } },
      }),
    },
    createElement(MemoryRouter, null, children),
  );

beforeEach(() => axios.get.mockReset());

describe("useAllRuns", () => {
  it("fetches every page of the run-test's executions, newest first", async () => {
    serveExecutions(150);
    const { result } = renderHook(() => useAllRuns(env, {}), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.runTests.detailExecutions("rt-1"),
      { params: { page: 1, limit: 100 } },
    );
    expect(axios.get).toHaveBeenCalledWith(
      endpoints.runTests.detailExecutions("rt-1"),
      { params: { page: 2, limit: 100 } },
    );
    expect(result.current.runs).toHaveLength(150);
    expect(result.current.capped).toBe(false);
    // Ordinals stamped across pages, the same numbers the table reads.
    expect(result.current.runs[0]).toMatchObject({
      id: "ex-150",
      label: "Run 150",
    });
    expect(result.current.runs[149]).toMatchObject({
      id: "ex-1",
      label: "Run 1",
    });
  });

  it("stops at the cap and says it is capped", async () => {
    serveExecutions(260);
    const { result } = renderHook(() => useAllRuns(env, {}), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(result.current.runs).toHaveLength(200);
    expect(result.current.capped).toBe(true);
    expect(result.current.runs[0].label).toBe("Run 260");
    expect(result.current.runs[199].label).toBe("Run 61");
  });

  it("honours a smaller cap", async () => {
    serveExecutions(30);
    const { result } = renderHook(() => useAllRuns(env, {}, { cap: 20 }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.get.mock.calls[0][1]).toEqual({
      params: { page: 1, limit: 20 },
    });
    expect(result.current.runs).toHaveLength(20);
    expect(result.current.capped).toBe(true);
  });

  it("fetches nothing without a run-test and reads the client runs", () => {
    const local = [{ id: "r1" }, { id: "r2" }];
    const { result } = renderHook(
      () => useAllRuns({ id: "env-2" }, { runs: local }, { cap: 1 }),
      { wrapper },
    );
    expect(axios.get).not.toHaveBeenCalled();
    expect(result.current).toEqual({
      runs: [{ id: "r1" }],
      capped: true,
      isLoading: false,
    });
  });

  it("serves the QA mock runs behind ?mockRuns=1", () => {
    const mockWrapper = ({ children }) =>
      createElement(
        QueryClientProvider,
        { client: new QueryClient() },
        createElement(
          MemoryRouter,
          { initialEntries: ["/?mockRuns=1"] },
          children,
        ),
      );
    const { result } = renderHook(() => useAllRuns(env, {}), {
      wrapper: mockWrapper,
    });
    expect(axios.get).not.toHaveBeenCalled();
    expect(result.current.runs).toEqual(MOCK_RUNS);
    expect(result.current.capped).toBe(false);
  });
});
