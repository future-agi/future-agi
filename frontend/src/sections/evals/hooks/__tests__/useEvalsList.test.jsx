import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    develop: {
      eval: {
        listEvalTemplateCharts: "/evals/list-charts/",
      },
    },
  },
}));

import { useEvalsListCharts } from "../useEvalsList";

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

const chart = {
  "eval-1": {
    chart: [{ timestamp: "2026-08-11T00:00:00", value: 7 }],
    error_rate: [{ timestamp: "2026-08-11T00:00:00", value: 2 }],
    run_count: 7,
  },
};

describe("useEvalsListCharts bounded-read contract", () => {
  beforeEach(() => vi.clearAllMocks());

  it("keeps exactness metadata with an exact chart", async () => {
    mocks.post.mockResolvedValue({
      data: {
        result: {
          charts: chart,
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          data_stale: false,
        },
      },
    });
    const { result } = renderHook(() => useEvalsListCharts(["eval-1"]), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      charts: chart,
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      data_stale: false,
    });
  });

  it("keeps cached stale charts and their visible stale marker", async () => {
    mocks.post.mockResolvedValue({
      data: {
        result: {
          charts: chart,
          query_complete: false,
          query_status: "stale",
          query_sampled: false,
          query_error_code: "read_budget_exceeded",
          data_stale: true,
        },
      },
    });
    const { result } = renderHook(() => useEvalsListCharts(["eval-1"]), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.charts).toEqual(chart);
    expect(result.current.data).toMatchObject({
      query_complete: false,
      query_status: "stale",
      query_error_code: "read_budget_exceeded",
      data_stale: true,
    });
  });

  it("does not expose a degraded zero-filled payload as real chart data", async () => {
    mocks.post.mockResolvedValue({
      data: {
        result: {
          charts: chart,
          query_complete: false,
          query_status: "degraded",
          query_sampled: false,
          query_error_code: "read_budget_exceeded",
          data_stale: false,
        },
      },
    });
    const { result } = renderHook(() => useEvalsListCharts(["eval-1"]), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.charts).toEqual({});
    expect(result.current.data).toMatchObject({
      query_complete: false,
      query_status: "degraded",
      query_error_code: "read_budget_exceeded",
    });
  });

  it("fails a metadata-less 2xx body closed", async () => {
    mocks.post.mockResolvedValue({ data: { result: { charts: chart } } });
    const { result } = renderHook(() => useEvalsListCharts(["eval-1"]), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.charts).toEqual({});
  });
});
