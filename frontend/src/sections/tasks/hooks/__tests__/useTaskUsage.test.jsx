import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    project: { getEvalTaskUsage: () => "/tracer/eval-task/get_usage/" },
  },
}));

import {
  TASK_USAGE_REQUEST_TIMEOUT_MS,
  useTaskUsageChart,
  useTaskUsageLogs,
} from "../useTaskUsage";

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

describe("task usage bounded query params", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      data: {
        result: {
          stats: {},
          chart: [],
          evals: [],
          logs: { count: 0, results: [], has_more: false },
          period_requested: "custom",
          period_used: "custom",
        },
      },
    });
  });

  it("sends the complete custom range for chart and log reads", async () => {
    const wrapper = createQueryWrapper();
    const dateRange = ["2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"];
    const expectedStart = "2026-07-01T00:00:00.000Z";
    const expectedEnd = "2026-08-01T00:00:00.000Z";

    renderHook(
      () =>
        useTaskUsageChart("task-1", {
          period: "30d",
          dateRange,
        }),
      { wrapper },
    );
    renderHook(
      () =>
        useTaskUsageLogs("task-1", {
          period: "30d",
          page: 2,
          pageSize: 50,
          dateRange,
        }),
      { wrapper },
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    const requests = mocks.get.mock.calls.map(([, options]) => options.params);
    expect(requests).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          start_date: expectedStart,
          end_date: expectedEnd,
          page: 1,
          page_size: 1,
        }),
        expect.objectContaining({
          start_date: expectedStart,
          end_date: expectedEnd,
          page: 3,
          page_size: 50,
          include_summary: false,
        }),
      ]),
    );
    for (const [, options] of mocks.get.mock.calls) {
      expect(options).toEqual(
        expect.objectContaining({
          signal: expect.any(AbortSignal),
          timeout: TASK_USAGE_REQUEST_TIMEOUT_MS,
        }),
      );
    }
    expect(TASK_USAGE_REQUEST_TIMEOUT_MS).toBe(INTERACTIVE_REQUEST_TIMEOUT_MS);
  });

  it("makes a same-day Custom selection one complete local calendar day", async () => {
    const wrapper = createQueryWrapper();
    renderHook(
      () =>
        useTaskUsageChart("task-1", {
          period: "30d",
          dateRange: ["2026-08-12T00:00:00Z", "2026-08-12T00:00:00Z"],
          endInclusive: true,
        }),
      { wrapper },
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    expect(mocks.get.mock.calls[0][1].params).toMatchObject({
      start_date: "2026-08-12T00:00:00.000Z",
      end_date: "2026-08-13T00:00:00.000Z",
    });
  });

  it("does not send custom bounds for a preset period", async () => {
    const wrapper = createQueryWrapper();
    renderHook(() => useTaskUsageChart("task-1", { period: "365d" }), {
      wrapper,
    });

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    expect(mocks.get.mock.calls[0][1].params).toMatchObject({
      eval_task_id: "task-1",
      period: "365d",
      page: 1,
      page_size: 1,
    });
    expect(mocks.get.mock.calls[0][1].params).not.toHaveProperty("start_date");
    expect(mocks.get.mock.calls[0][1].params).not.toHaveProperty("end_date");
  });

  it("fails malformed usage responses instead of publishing zero data", async () => {
    mocks.get.mockResolvedValueOnce({ data: { result: {} } });
    const wrapper = createQueryWrapper();
    const { result } = renderHook(() => useTaskUsageChart("task-1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toMatchObject({
      code: "task_usage_invalid_response",
    });
  });
});
