import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useGetTraceDetail } from "../trace-detail";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: { getTrace: (id) => `/tracer/trace/${id}/` },
  },
}));

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

describe("useGetTraceDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockResolvedValue({ data: { result: {} } });
  });

  it("pins the read to the project the trace was opened from", async () => {
    renderHook(() => useGetTraceDetail("trace-1", { projectId: "project-1" }), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    expect(axios.get).toHaveBeenCalledWith("/tracer/trace/trace-1/", {
      params: { project_id: "project-1" },
    });
  });

  it("omits project_id when the caller has no project in context", async () => {
    renderHook(() => useGetTraceDetail("trace-1"), {
      wrapper: createQueryWrapper(),
    });

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    expect(axios.get).toHaveBeenCalledWith("/tracer/trace/trace-1/", {
      params: undefined,
    });
  });

  it("sends the run's test_execution_id with the project when opened from a call", async () => {
    renderHook(
      () =>
        useGetTraceDetail("trace-1", {
          projectId: "hidden-project",
          testExecutionId: "run-1",
        }),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    expect(axios.get).toHaveBeenCalledWith("/tracer/trace/trace-1/", {
      params: { project_id: "hidden-project", test_execution_id: "run-1" },
    });
  });

  it("retains the actual 503 error and retries the same bounded endpoint", async () => {
    const error = { statusCode: 503, result: "Service unavailable" };
    axios.get.mockRejectedValueOnce(error);
    const { result } = renderHook(
      () => useGetTraceDetail("trace-1", { projectId: "p" }),
      { wrapper: createQueryWrapper() },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual(error);
    expect(result.current.data).toBeUndefined();

    const emptyTrace = { observation_spans: [] };
    axios.get.mockResolvedValueOnce({ data: { result: emptyTrace } });
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(emptyTrace);
    expect(result.current.error).toBeNull();
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.get.mock.calls[1]).toEqual(axios.get.mock.calls[0]);
  });

  it("does not carry the old trace's spans or error into another trace identity", async () => {
    let resolveOld;
    axios.get.mockImplementation((url) =>
      url.endsWith("trace-1/")
        ? new Promise((resolve) => {
            resolveOld = resolve;
          })
        : Promise.resolve({
            data: {
              result: { trace: { id: "trace-2" }, observation_spans: [] },
            },
          }),
    );
    const { result, rerender } = renderHook(
      ({ traceId }) => useGetTraceDetail(traceId, { projectId: "p" }),
      { wrapper: createQueryWrapper(), initialProps: { traceId: "trace-1" } },
    );
    await waitFor(() => expect(resolveOld).toEqual(expect.any(Function)));
    rerender({ traceId: "trace-2" });
    await waitFor(() => expect(result.current.data?.trace.id).toBe("trace-2"));
    await act(async () => {
      resolveOld({ data: { result: { trace: { id: "trace-1" } } } });
    });
    expect(result.current.data.trace.id).toBe("trace-2");
    expect(result.current.isError).toBe(false);
  });

  it("keeps one run's copy of a trace apart from the plain read in the cache", async () => {
    const wrapper = createQueryWrapper();
    renderHook(() => useGetTraceDetail("trace-1", { projectId: "p" }), {
      wrapper,
    });
    renderHook(
      () =>
        useGetTraceDetail("trace-1", {
          projectId: "p",
          testExecutionId: "run-1",
        }),
      { wrapper },
    );

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  });
});


it("does not reuse a fresh coverage-blind trace detail cache entry", async () => {
  axios.get.mockReset();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(["trace-detail", "trace-1", "p"], {
    data: { result: { summary: { total_cost: 0 } } },
  });
  const capture = { scope: "observed_requests", source_summary_present: true,
    request_count: 0, coverage_unknown: true };
  axios.get.mockResolvedValueOnce({ data: { result: { capture_coverage: capture } } });
  const { result } = renderHook(() => useGetTraceDetail("trace-1", { projectId: "p" }), {
    wrapper: ({ children }) => React.createElement(QueryClientProvider, { client: queryClient }, children),
  });
  await waitFor(() => expect(result.current.data?.capture_coverage).toEqual(capture));
});
