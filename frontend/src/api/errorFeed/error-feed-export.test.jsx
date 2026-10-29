import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { useExportErrorFeedTraces } from "./error-feed";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    errorFeed: {
      overview: (clusterId) => `/tracer/feed/issues/${clusterId}/overview/`,
    },
  },
}));

const createWrapper = () => {
  const queryClient = new QueryClient();
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("Error Feed traces export", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the overview at the 200-card max in one request", async () => {
    const overview = { representative_traces: [], representative_total: 0 };
    axios.get.mockResolvedValue({ data: { result: overview } });

    const { result } = renderHook(() => useExportErrorFeedTraces(), {
      wrapper: createWrapper(),
    });

    await expect(result.current.mutateAsync("c1")).resolves.toEqual(overview);
    expect(axios.get).toHaveBeenCalledOnce();
    expect(axios.get).toHaveBeenCalledWith("/tracer/feed/issues/c1/overview/", {
      params: { rep_limit: 200 },
    });
  });
});
