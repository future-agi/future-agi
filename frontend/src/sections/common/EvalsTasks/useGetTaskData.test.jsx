import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { useGetTaskData } from "./common";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    project: {
      getEvalTaskDetails: (taskId) => `/eval-task/details/?eval_id=${taskId}`,
    },
  },
}));

const validResponse = {
  data: {
    status: true,
    result: {
      id: "task-1",
      name: "Task one",
      project_id: "project-1",
      project_name: "Project one",
      status: "completed",
      filters_applied: {},
      evals_applied: [],
      spans_limit: 100,
      sampling_rate: 100,
      run_type: "continuous",
      row_type: "spans",
    },
  },
};

const createWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: 3 } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("useGetTaskData", () => {
  beforeEach(() => axiosGetMock.mockReset());

  it("selects a validated task while preserving the raw axios cache contract", async () => {
    axiosGetMock.mockResolvedValue(validResponse);

    const { result } = renderHook(() => useGetTaskData("task-1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(validResponse.data.result);
    expect(axiosGetMock).toHaveBeenCalledWith(
      "/eval-task/details/?eval_id=task-1",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: INTERACTIVE_REQUEST_TIMEOUT_MS,
      }),
    );
  });

  it("does not retry or keep polling after a failed visible read", async () => {
    axiosGetMock.mockResolvedValue({ data: { status: true, result: {} } });

    const { result } = renderHook(
      () => useGetTaskData("task-1", { refetchInterval: 5 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 25));
    expect(result.current.error).toMatchObject({
      code: "eval_task_detail_invalid_response",
    });
    expect(axiosGetMock).toHaveBeenCalledOnce();
  });
});
