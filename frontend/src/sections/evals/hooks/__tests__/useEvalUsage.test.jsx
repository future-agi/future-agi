import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    develop: { eval: { getEvalUsage: (id) => `/eval/${id}/usage/` } },
  },
}));

import { useEvalUsageChart, useEvalUsageLogs } from "../useEvalUsage";

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
    renderHook(() => useEvalUsageChart("t1", "1d", "Today", null), { wrapper });
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));

    renderHook(() => useEvalUsageChart("t1", "1d", "Today", null), { wrapper });
    await flush();
    // Second hook hit the cache under the identical key — still one request.
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("does not fetch for an incomplete Custom range", async () => {
    const wrapper = createQueryWrapper();
    renderHook(() => useEvalUsageChart("t1", "30d", "Custom", null), { wrapper });
    renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "Custom", dateFilter: [null, null] }),
      { wrapper },
    );
    await flush();
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

  it("maps result.table → table and result.logs → pagination", async () => {
    mocks.get.mockResolvedValue({
      data: { result: { table: [{ row_id: "a" }], logs: { total: 5, page: 0 } } },
    });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(result.current.data.table).toHaveLength(1);
    expect(result.current.data.pagination).toEqual({ total: 5, page: 0 });
  });
});
