import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    develop: { eval: { getEvalUsage: (id) => `/eval/${id}/usage/` } },
  },
}));

import { useEvalUsageLogs } from "../useEvalUsage";

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
}

const flush = () => new Promise((r) => setTimeout(r, 20));

describe("useEvalUsage date params", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      data: { result: { stats: {}, chart: [], table: [], logs: {} } },
    });
  });

  it("keeps the Today query key stable so it does not self-refetch in a loop", async () => {
    // Two independent invocations of the Today window must hash to the same
    // query key — the upper bound is floored to the minute, so a fresh-
    // millisecond `new Date()` can't mint a new key and re-fetch forever.
    const wrapper = createQueryWrapper();
    renderHook(
      () => useEvalUsageLogs("t1", { period: "1d", dateOption: "Today" }),
      { wrapper },
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));

    renderHook(
      () => useEvalUsageLogs("t1", { period: "1d", dateOption: "Today" }),
      { wrapper },
    );
    await act(flush);
    // Second hook hit the cache under the identical key — still one request.
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("does not fetch for an incomplete Custom range", async () => {
    const wrapper = createQueryWrapper();
    renderHook(
      () =>
        useEvalUsageLogs("t1", {
          dateOption: "Custom",
          dateFilter: [null, null],
        }),
      { wrapper },
    );
    await act(flush);
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("sends explicit start_date/end_date for a complete Custom range", async () => {
    const wrapper = createQueryWrapper();
    renderHook(
      () =>
        useEvalUsageLogs("t1", {
          dateOption: "Custom",
          dateFilter: ["2026-01-01", "2026-01-31"],
        }),
      { wrapper },
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    const { params } = mocks.get.mock.calls[0][1];
    expect(params.start_date).toBeTruthy();
    expect(params.end_date).toBeTruthy();
  });
});

describe("useEvalUsageLogs response mapping", () => {
  beforeEach(() => vi.clearAllMocks());

  it("maps the complete usage payload with one request", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          stats: { total_runs: 5 },
          chart: [{ timestamp: "2026-01-01T00:00:00Z", calls: 2 }],
          table: [{ row_id: "a" }],
          logs: { total: 5, page: 0 },
        },
      },
    });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(result.current.data.stats).toEqual({ total_runs: 5 });
    expect(result.current.data.chart).toHaveLength(1);
    expect(result.current.data.table).toHaveLength(1);
    expect(result.current.data.pagination).toEqual({ total: 5, page: 0 });
  });
});
