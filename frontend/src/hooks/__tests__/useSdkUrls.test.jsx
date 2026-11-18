import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useSdkUrls } from "src/hooks/useSdkUrls";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    project: { getCodeBlockTracer: "/tracer/project/project_sdk_code/" },
  },
}));

function setup(options) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  wrapper.propTypes = { children: PropTypes.node };
  return renderHook(() => useSdkUrls(options), { wrapper });
}

const answer = (result) => mocks.get.mockResolvedValue({ data: { result } });

describe("useSdkUrls", () => {
  beforeEach(() => mocks.get.mockReset());
  afterEach(cleanup);

  it("gives a self-hosted install's API and collector URLs from the backend", async () => {
    answer({
      keys: {},
      sdk_urls: {
        fi_base_url: "http://localhost:8001/",
        fi_traces_url: "http://localhost:4319",
      },
    });

    const { result } = setup();

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(mocks.get).toHaveBeenCalledWith(
      "/tracer/project/project_sdk_code/",
      { params: { project_type: "observe" } },
    );
    expect(result.current).toMatchObject({
      apiUrl: "http://localhost:8001",
      tracesUrl: "http://localhost:4319",
    });
  });

  it("gives none on Future AGI Cloud, which sends no sdk_urls", async () => {
    answer({ keys: {} });

    const { result } = setup();

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current).toMatchObject({ apiUrl: null, tracesUrl: null });
  });

  it("never asks before sign-in", () => {
    const { result } = setup({ enabled: false });

    expect(mocks.get).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({
      isLoading: false,
      apiUrl: null,
      tracesUrl: null,
    });
  });
});
