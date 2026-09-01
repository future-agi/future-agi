import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
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

const exactResponse = () => ({
  data: {
    status: true,
    result: {
      nodes: [],
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
