import React from "react";
import PropTypes from "prop-types";
import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    develop: { eval: { getEvalUsage: (id) => `/eval/${id}/usage/` } },
  },
}));

import { useEvalUsageChart, useEvalUsageLogs } from "../useEvalUsage";
import { AGGREGATION_POLL_MAX_ATTEMPTS } from "src/utils/queryReadState";

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
const exactResult = (result = {}) => ({
  query_complete: true,
  query_status: "complete",
  query_sampled: false,
  query_completed_at: "2026-08-03T02:00:00Z",
  stats: {},
  chart: [],
  table: [],
  logs: {},
  ...result,
});

afterEach(() => {
  vi.useRealTimers();
  mocks.get.mockReset();
});

describe("useEvalUsage date params", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      data: { result: exactResult() },
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
    renderHook(() => useEvalUsageChart("t1", "30d", "Custom", null), {
      wrapper,
    });
    renderHook(
      () =>
        useEvalUsageLogs("t1", {
          dateOption: "Custom",
          dateFilter: [null, null],
        }),
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

  it("uses refresh=true only for an explicit chart refresh", async () => {
    mocks.get.mockResolvedValue({ data: { result: exactResult() } });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get.mock.calls[0][1].params).not.toHaveProperty("refresh");

    await act(async () => result.current.refresh());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(mocks.get.mock.calls[1][1].params.refresh).toBe(true);
    expect(result.current.data.queryCompletedAt).toBe(
      "2026-08-03T02:00:00.000Z",
    );
    expect(result.current.data).toMatchObject({
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
    });
  });

  it("fails metadata-less aggregation responses closed", async () => {
    mocks.get.mockResolvedValue({
      data: { result: { stats: {}, chart: [] } },
    });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });

  it("polls a cold pending chart with an ordinary request until exact", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            stats: {},
            chart: [],
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: exactResult({
            chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 2 }],
          }),
        },
      });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(true);
    expect(result.current.data).toMatchObject({
      query_complete: false,
      query_status: "pending",
      query_sampled: false,
      query_refreshing: true,
    });
    expect(mocks.get).toHaveBeenCalledOnce();

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1][1].params).not.toHaveProperty("refresh");
    expect(result.current.data?.queryPending).toBe(false);
    expect(result.current.data?.chart).toHaveLength(1);
  });

  it("makes retry available after three chart polling transport failures and recovers explicitly", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            stats: {},
            chart: [],
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockRejectedValueOnce(new Error("transport failed 1"))
      .mockRejectedValueOnce(new Error("transport failed 2"))
      .mockRejectedValueOnce(new Error("transport failed 3"));
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(7_010));

    expect(mocks.get).toHaveBeenCalledTimes(4);
    expect(result.current.isError).toBe(true);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);

    mocks.get.mockResolvedValueOnce({
      data: {
        result: exactResult({
          chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 2 }],
        }),
      },
    });
    await act(async () => result.current.refresh());

    expect(mocks.get).toHaveBeenCalledTimes(5);
    expect(mocks.get.mock.calls[4][1].params.refresh).toBe(true);
    expect(result.current.isError).toBe(false);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.queryRefreshing).toBe(false);
    expect(result.current.data?.chart).toHaveLength(1);
  });

  it("stops chart polling immediately when a pending job returns an invalid 2xx contract", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            stats: {},
            chart: [],
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: { result: { stats: {}, chart: [] } },
      });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(1_010));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.isError).toBe(true);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);

    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("bounds a server-confirmed chart refresh and accepts completion after explicit retry", async () => {
    vi.useFakeTimers();
    const pending = {
      data: {
        result: {
          stats: {},
          chart: [],
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    };
    mocks.get.mockResolvedValue(pending);
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageChart("t1", "30d", "30D", null),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(result.current.data?.queryPending).toBe(true);

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    const boundedRequestCount = mocks.get.mock.calls.length;
    expect(result.current.isError).toBe(false);
    expect(result.current.isPollingPaused).toBe(true);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);
    expect(boundedRequestCount).toBeLessThanOrEqual(
      AGGREGATION_POLL_MAX_ATTEMPTS + 1,
    );

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount);

    mocks.get.mockResolvedValueOnce({
      data: {
        result: exactResult({
          chart: [{ timestamp: "2026-08-03T00:00:00Z", calls: 2 }],
        }),
      },
    });
    await act(async () => result.current.refresh());
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount + 1);
    expect(result.current.isError).toBe(false);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.chart).toHaveLength(1);
  });
});

describe("useEvalUsageLogs response mapping", () => {
  beforeEach(() => vi.clearAllMocks());

  it("maps result.table → table and result.logs → pagination", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: exactResult({
          table: [{ row_id: "a" }],
          logs: { total: 5, page: 0 },
        }),
      },
    });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(result.current.data.table).toHaveLength(1);
    expect(result.current.data.pagination).toEqual({ total: 5, page: 0 });
    expect(result.current.data).toMatchObject({
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
    });
  });

  it("keeps the previous exact page visible while the next page loads", async () => {
    let resolveNextPage;
    const nextPageRequest = new Promise((resolve) => {
      resolveNextPage = resolve;
    });
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: exactResult({
            table: [{ row_id: "page-0" }],
            logs: { total: 2, page: 0 },
          }),
        },
      })
      .mockImplementationOnce(() => nextPageRequest);

    const wrapper = createQueryWrapper();
    const { result, rerender } = renderHook(
      ({ page }) =>
        useEvalUsageLogs("t1", {
          page,
          pageSize: 1,
          dateOption: "30D",
        }),
      { wrapper, initialProps: { page: 0 } },
    );

    await waitFor(() =>
      expect(result.current.data?.table?.[0]?.row_id).toBe("page-0"),
    );
    rerender({ page: 1 });
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));

    expect(result.current.isPlaceholderData).toBe(true);
    expect(result.current.data?.table?.[0]?.row_id).toBe("page-0");

    await act(async () => {
      resolveNextPage({
        data: {
          result: exactResult({
            table: [{ row_id: "page-1" }],
            logs: { total: 2, page: 1 },
          }),
        },
      });
    });
    await waitFor(() =>
      expect(result.current.data?.table?.[0]?.row_id).toBe("page-1"),
    );
    expect(result.current.isPlaceholderData).toBe(false);
  });

  it("polls the logs identity independently until its exact page is ready", async () => {
    vi.useFakeTimers();
    const exactRows = Array.from({ length: 24 }, (_, index) => ({
      row_id: `row-${index}`,
    }));
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            table: [],
            logs: { total: 0, page: 0 },
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: exactResult({
            table: exactRows,
            logs: { total: 24, page: 0 },
          }),
        },
      });

    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () =>
        useEvalUsageLogs("t1", {
          page: 0,
          pageSize: 25,
          dateOption: "30D",
        }),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.table).toHaveLength(0);
    expect(mocks.get).toHaveBeenCalledOnce();
    expect(mocks.get.mock.calls[0][1].params.page_size).toBe(25);

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await act(async () => vi.advanceTimersByTimeAsync(10));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1][1].params).not.toHaveProperty("refresh");
    expect(result.current.data?.queryPending).toBe(false);
    expect(result.current.data?.table).toHaveLength(24);
    expect(result.current.data?.pagination.total).toBe(24);
  });

  it("makes retry available after three log polling transport failures and recovers explicitly", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            table: [],
            logs: {},
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockRejectedValueOnce(new Error("transport failed 1"))
      .mockRejectedValueOnce(new Error("transport failed 2"))
      .mockRejectedValueOnce(new Error("transport failed 3"));
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(7_010));

    expect(mocks.get).toHaveBeenCalledTimes(4);
    expect(result.current.isError).toBe(true);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);

    mocks.get.mockResolvedValueOnce({
      data: {
        result: exactResult({
          table: [{ row_id: "recovered" }],
          logs: { total: 1, page: 0 },
        }),
      },
    });
    await act(async () => result.current.refresh());

    expect(mocks.get).toHaveBeenCalledTimes(5);
    expect(mocks.get.mock.calls[4][1].params.refresh).toBe(true);
    expect(result.current.isError).toBe(false);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.queryRefreshing).toBe(false);
    expect(result.current.data?.table?.[0]?.row_id).toBe("recovered");
  });

  it("stops log polling immediately when a pending job returns an invalid 2xx contract", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            table: [],
            logs: {},
            query_complete: false,
            query_status: "pending",
            query_sampled: false,
            query_refreshing: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: { result: { table: [], logs: {} } },
      });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(1_010));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.isError).toBe(true);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);

    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("bounds a server-confirmed log refresh and resumes after explicit retry", async () => {
    vi.useFakeTimers();
    mocks.get.mockResolvedValue({
      data: {
        result: {
          table: [],
          logs: {},
          query_complete: false,
          query_status: "pending",
          query_sampled: false,
          query_refreshing: true,
        },
      },
    });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(
      () => useEvalUsageLogs("t1", { dateOption: "30D" }),
      { wrapper },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    await act(async () => vi.advanceTimersByTimeAsync(500_000));

    const boundedRequestCount = mocks.get.mock.calls.length;
    expect(result.current.isError).toBe(false);
    expect(result.current.isPollingPaused).toBe(true);
    expect(result.current.data?.queryPending).toBe(true);
    expect(result.current.data?.queryRefreshing).toBe(false);
    expect(boundedRequestCount).toBeLessThanOrEqual(
      AGGREGATION_POLL_MAX_ATTEMPTS + 1,
    );

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount);

    mocks.get.mockResolvedValueOnce({
      data: {
        result: exactResult({
          table: [{ row_id: "eventual" }],
          logs: { total: 1, page: 0 },
        }),
      },
    });
    await act(async () => result.current.refresh());
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount + 1);
    expect(result.current.isPollingPaused).toBe(false);
    expect(result.current.data?.queryPending).toBe(false);
    expect(result.current.data?.table?.[0]?.row_id).toBe("eventual");
  });
});
