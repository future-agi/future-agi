import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
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
    renderHook(() => useGetTraceDetail("trace-1", "project-1"), {
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
});
