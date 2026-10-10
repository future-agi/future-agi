import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useGatewayHealthCheck } from "./useGatewayHealthCheck";

const { post, enqueueSnackbar } = vi.hoisted(() => ({
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar }));
vi.mock("src/utils/axios", () => ({
  default: { post },
  endpoints: {
    gateway: { healthCheck: (id) => `/agentcc/gateways/${id}/health_check/` },
  },
}));

function renderHealthCheck() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(useGatewayHealthCheck, { wrapper });
}

beforeEach(() => vi.clearAllMocks());

describe("useGatewayHealthCheck", () => {
  it("bounds the POST so a stalled health check cannot leave the button disabled forever", async () => {
    post.mockResolvedValue({ data: { result: { status: "healthy" } } });
    const { result } = renderHealthCheck();
    await act(async () => result.current.mutateAsync("default"));
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/default/health_check/",
      {},
      { timeout: 30000 },
    );
  });

  it("shows actionable timeout feedback from the shared Axios error shape", async () => {
    const error = {
      transportCode: "ECONNABORTED",
      message: "timeout of 30000ms exceeded",
    };
    post.mockRejectedValue(error);
    const { result } = renderHealthCheck();
    await act(async () => {
      await expect(result.current.mutateAsync("default")).rejects.toEqual(
        error,
      );
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Health check timed out — the gateway did not respond. Check that it is reachable, then try again.",
      { variant: "error" },
    );
  });
});
