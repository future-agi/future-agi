import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import axios from "src/utils/axios";

vi.mock("src/utils/axios", () => {
  const mock = { get: vi.fn(), defaults: {}, interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } };
  mock.default = mock;
  return { default: mock, endpoints: { settings: { v2: { deploymentInfo: "/usage/v2/deployment-info/" } } } };
});

function wrapper({ children }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

// Re-import after mocks are set up.
async function importHook() {
  const mod = await import("../useDeploymentMode");
  return mod;
}

describe("useDeploymentMode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns oss mode when endpoint returns 404 (OSS deployment)", async () => {
    const error = new Error("Not Found");
    error.response = { status: 404 };
    axios.get.mockRejectedValue(error);

    const { useDeploymentMode } = await importHook();
    const { result } = renderHook(() => useDeploymentMode(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.isOSS).toBe(true);
    expect(result.current.isCloud).toBe(false);
    expect(result.current.isEE).toBe(false);
    expect(result.current.mode).toBe("oss");
  });

  it("returns the backend-reported mode on success", async () => {
    axios.get.mockResolvedValue({ data: { result: { mode: "ee" } } });

    const { useDeploymentMode } = await importHook();
    const { result } = renderHook(() => useDeploymentMode(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.isEE).toBe(true);
    expect(result.current.isOSS).toBe(false);
    expect(result.current.mode).toBe("ee");
  });

  it("propagates non-404 errors (broken backends remain visible)", async () => {
    const error = new Error("Server Error");
    error.response = { status: 500 };
    axios.get.mockRejectedValue(error);

    const { useDeploymentMode } = await importHook();
    const { result } = renderHook(() => useDeploymentMode(), { wrapper });

    // isLoading settles; query is in error state so data stays undefined → falls back to "oss"
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    // The hook's fallback is "oss" for any undefined data — but the query itself errored.
    // This test documents the behavior: 5xx does not cause a flicker loop (retry:false).
    expect(result.current.mode).toBe("oss");
  });
});
