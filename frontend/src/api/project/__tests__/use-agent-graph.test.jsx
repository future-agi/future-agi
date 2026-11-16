import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  readQuery: mocks.get,
  endpoints: {
    project: { getAgentGraph: () => "/tracer/trace/agent-graph/" },
  },
}));

import { useAgentGraph } from "../agent-graph";
import { AGGREGATION_POLL_MAX_ATTEMPTS } from "src/utils/queryReadState";

const pendingResponse = () => ({
  data: {
    status: true,
    result: {
      nodes: [],
      edges: [],
      path_edges: [],
      query_complete: false,
      query_status: "pending",
      query_sampled: false,
      query_refreshing: true,
      query_refresh_failed: false,
    },
  },
});

const exactResponse = (name = "agent") => ({
  data: {
    status: true,
    result: {
      nodes: [
        {
          id: `agent:${name}`,
          name,
          type: "agent",
          span_count: 1,
          avg_latency_ms: 10,
          total_tokens: 1,
          total_cost: 0,
          error_count: 0,
          trace_count: 1,
        },
      ],
      edges: [],
      path_edges: [],
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      query_refreshing: false,
      query_refresh_failed: false,
    },
  },
});

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function QueryWrapper({ children }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
}

afterEach(() => {
  vi.useRealTimers();
  mocks.get.mockReset();
});

describe("useAgentGraph bounded polling", () => {
  it("pauses without an error when a healthy exact job outlives the poll budget", async () => {
    vi.useFakeTimers();
    mocks.get.mockResolvedValue(pendingResponse());
    const { result } = renderHook(() => useAgentGraph("project-1"), {
      wrapper: createQueryWrapper(),
    });

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isError).toBe(false);

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    const boundedRequestCount = mocks.get.mock.calls.length;
    expect(boundedRequestCount).toBeLessThanOrEqual(
      AGGREGATION_POLL_MAX_ATTEMPTS + 1,
    );
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isError).toBe(false);
    expect(result.current.pollingPaused).toBe(true);

    await act(async () => vi.advanceTimersByTimeAsync(500_000));
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount);

    mocks.get.mockResolvedValueOnce(exactResponse());
    await act(async () => result.current.refresh());
    expect(mocks.get).toHaveBeenCalledTimes(boundedRequestCount + 1);
    expect(mocks.get.mock.calls.at(-1)[1].params.refresh).toBe(true);
    expect(result.current.isError).toBe(false);
    expect(result.current.pollingPaused).toBe(false);
    expect(result.current.data?.query_complete).toBe(true);
  });

  it("keeps repeated polling transport failures terminal and retryable", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce(pendingResponse())
      .mockRejectedValueOnce(new Error("transport failed 1"))
      .mockRejectedValueOnce(new Error("transport failed 2"))
      .mockRejectedValueOnce(new Error("transport failed 3"));
    const { result } = renderHook(() => useAgentGraph("project-1"), {
      wrapper: createQueryWrapper(),
    });

    await act(async () => vi.advanceTimersByTimeAsync(7_010));

    expect(mocks.get).toHaveBeenCalledTimes(4);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isError).toBe(true);
    expect(result.current.pollingPaused).toBe(false);

    mocks.get.mockResolvedValueOnce(exactResponse());
    await act(async () => result.current.refresh());
    expect(mocks.get).toHaveBeenCalledTimes(5);
    expect(result.current.isError).toBe(false);
    expect(result.current.pollingPaused).toBe(false);
  });
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const tagFilters = (value) => [
  {
    column_id: "tag",
    filter_config: {
      col_type: "SYSTEM_METRIC",
      filter_type: "text",
      filter_op: "in",
      filter_value: [value],
    },
  },
];

const unavailable = () =>
  Object.assign(new Error("assignment read unavailable"), {
    response: { status: 503, data: { code: "service_unavailable" } },
  });

describe("useAgentGraph observer scope", () => {
  it("fences both observers before a deduplicated new-key fetch rejects and recovers", async () => {
    const held = deferred();
    mocks.get
      .mockResolvedValueOnce(exactResponse("manual-primary"))
      .mockResolvedValueOnce(exactResponse("manual-compare"))
      .mockReturnValueOnce(held.promise);
    const { result, rerender } = renderHook(
      ({ primary, compare }) => ({
        primary: useAgentGraph("project-1", tagFilters(primary)),
        compare: useAgentGraph("project-1", tagFilters(compare)),
      }),
      {
        wrapper: createQueryWrapper(),
        initialProps: { primary: "manual-primary", compare: "manual-compare" },
      },
    );
    await waitFor(() => {
      expect(result.current.primary.data?.nodes[0].name).toBe("manual-primary");
      expect(result.current.compare.data?.nodes[0].name).toBe("manual-compare");
    });

    rerender({ primary: "sdk", compare: "sdk" });
    expect(mocks.get).toHaveBeenCalledTimes(3);
    for (const observer of Object.values(result.current)) {
      expect(observer.data).toBeNull();
      expect(observer.isLoading).toBe(true);
    }
    await act(async () => held.reject(unavailable()));
    await waitFor(() => {
      for (const observer of Object.values(result.current)) {
        expect(observer.data).toBeNull();
        expect(observer.isError).toBe(true);
        expect(observer.error.response.data).toEqual({
          code: "service_unavailable",
        });
      }
    });
    const recovery = deferred();
    mocks.get.mockReturnValueOnce(recovery.promise);
    await act(async () => {
      const primary = result.current.primary.refresh();
      const compare = result.current.compare.refresh();
      recovery.resolve(exactResponse("sdk"));
      await Promise.all([primary, compare]);
    });
    await waitFor(() => {
      for (const observer of Object.values(result.current)) {
        expect(observer.data?.nodes[0].name).toBe("sdk");
        expect(observer.isError).toBe(false);
      }
    });
    expect(mocks.get).toHaveBeenCalledTimes(4);
  });

  it("ignores late old-key resolution while another observer keeps that request alive", async () => {
    const oldRefresh = deferred();
    const newRead = deferred();
    mocks.get.mockResolvedValueOnce(exactResponse("old"));
    const { result, rerender } = renderHook(
      ({ moving }) => ({
        moving: useAgentGraph("project-1", tagFilters(moving)),
        staying: useAgentGraph("project-1", tagFilters("old")),
      }),
      { wrapper: createQueryWrapper(), initialProps: { moving: "old" } },
    );
    await waitFor(() =>
      expect(result.current.moving.data?.nodes[0].name).toBe("old"),
    );
    mocks.get
      .mockReturnValueOnce(oldRefresh.promise)
      .mockReturnValueOnce(newRead.promise);
    let refreshPromise;
    act(() => {
      refreshPromise = result.current.moving.refresh();
    });
    rerender({ moving: "new" });
    await act(async () => {
      oldRefresh.resolve(exactResponse("late-old"));
      await refreshPromise;
    });
    await act(async () => newRead.reject(unavailable()));
    await waitFor(() => {
      expect(result.current.staying.data?.nodes[0].name).toBe("late-old");
      expect(result.current.moving.data).toBeNull();
      expect(result.current.moving.isError).toBe(true);
    });
    expect(mocks.get).toHaveBeenCalledTimes(3);
  });

  it("retains each observer's same-key exact snapshot through pending refresh failure", async () => {
    mocks.get.mockResolvedValueOnce(exactResponse("shared"));
    const { result } = renderHook(
      () => ({
        primary: useAgentGraph("project-1", tagFilters("shared")),
        compare: useAgentGraph("project-1", tagFilters("shared")),
      }),
      { wrapper: createQueryWrapper() },
    );
    await waitFor(() =>
      expect(result.current.compare.data?.nodes[0].name).toBe("shared"),
    );
    mocks.get.mockResolvedValueOnce(pendingResponse());
    await act(async () => result.current.primary.refresh());
    await waitFor(() => {
      for (const observer of Object.values(result.current)) {
        expect(observer.data?.nodes[0].name).toBe("shared");
        expect(observer.queryReadState).toBe("pending");
      }
    });
    mocks.get.mockRejectedValueOnce(unavailable());
    await act(async () => result.current.compare.refresh());
    await waitFor(() => {
      for (const observer of Object.values(result.current)) {
        expect(observer.data?.nodes[0].name).toBe("shared");
        expect(observer.isError).toBe(false);
        expect(observer.error.response.status).toBe(503);
      }
    });
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(mocks.get.mock.calls[1][1].params.refresh).toBe(true);
    expect(mocks.get.mock.calls[2][1].params.refresh).toBe(true);
  });

  it("uses completed cache on a key revisit without fetching or retaining a different key", async () => {
    mocks.get
      .mockResolvedValueOnce(exactResponse("cached"))
      .mockResolvedValueOnce(exactResponse("other"));
    const { result, rerender } = renderHook(
      ({ name }) => useAgentGraph("project-1", tagFilters(name)),
      { wrapper: createQueryWrapper(), initialProps: { name: "cached" } },
    );
    await waitFor(() =>
      expect(result.current.data?.nodes[0].name).toBe("cached"),
    );
    rerender({ name: "other" });
    await waitFor(() =>
      expect(result.current.data?.nodes[0].name).toBe("other"),
    );
    rerender({ name: "cached" });
    expect(result.current.data?.nodes[0].name).toBe("cached");
    expect(mocks.get).toHaveBeenCalledTimes(2);
    mocks.get.mockResolvedValueOnce(pendingResponse());
    await act(async () => result.current.refresh());
    await waitFor(() => {
      expect(result.current.queryReadState).toBe("pending");
      expect(result.current.data?.nodes[0].name).toBe("cached");
    });
  });

  it("keeps distinct project/filter errors and snapshots independent", async () => {
    mocks.get
      .mockResolvedValueOnce(exactResponse("primary"))
      .mockResolvedValueOnce(exactResponse("compare"));
    const { result, rerender } = renderHook(
      ({ project }) => ({
        primary: useAgentGraph(project, tagFilters("primary")),
        compare: useAgentGraph("project-1", tagFilters("compare")),
      }),
      { wrapper: createQueryWrapper(), initialProps: { project: "project-1" } },
    );
    await waitFor(() =>
      expect(result.current.compare.data?.nodes[0].name).toBe("compare"),
    );
    mocks.get.mockRejectedValueOnce(unavailable());
    rerender({ project: "project-2" });
    await waitFor(() => expect(result.current.primary.isError).toBe(true));
    expect(result.current.primary.data).toBeNull();
    expect(result.current.compare.data?.nodes[0].name).toBe("compare");
    expect(result.current.compare.isError).toBe(false);
  });
});

describe("useAgentGraph revisits", () => {
  it("asks the server again when the graph remounts on the same window", async () => {
    // The toolbar window is hour-stable, so a remount replays the same key.
    // The server serves the cached graph and revalidates it when it is old;
    // a client-side cache hit would never learn that.
    mocks.get.mockResolvedValue(exactResponse());
    const wrapper = createQueryWrapper();
    const first = renderHook(() => useAgentGraph("project-1"), { wrapper });
    await act(async () => {});
    expect(mocks.get).toHaveBeenCalledTimes(1);
    first.unmount();

    renderHook(() => useAgentGraph("project-1"), { wrapper });
    await act(async () => {});

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls.at(-1)[1].params.refresh).toBeUndefined();
  });
});
