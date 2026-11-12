import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { handleError } from "src/utils/queryErrorHandler";
import {
  HEALTH_CHECK_TIMEOUT_MESSAGE,
  useGatewayHealthCheck,
} from "./useGatewayHealthCheck";

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
  // Same app-wide mutation error handler as src/app.jsx, so a hook that also
  // toasts on its own would show up here as a duplicate.
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
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

  it("reports a client timeout without claiming the gateway is down", async () => {
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
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(HEALTH_CHECK_TIMEOUT_MESSAGE, {
      variant: "warning",
    });
    expect(HEALTH_CHECK_TIMEOUT_MESSAGE).not.toMatch(/did not respond/);
  });

  async function failWith(error) {
    post.mockRejectedValue(error);
    const { result } = renderHealthCheck();
    await act(async () => {
      await expect(result.current.mutateAsync("default")).rejects.toEqual(
        error,
      );
    });
  }

  it("shows exactly one sanitized toast for a failed probe", async () => {
    await failWith({
      statusCode: 400,
      detail: "The gateway could not be reached.",
      result: {
        status: "unreachable",
        error: "The gateway could not be reached.",
        last_health_check: "2026-11-10T00:20:01.049093+00:00",
      },
    });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Health check failed: The gateway could not be reached.",
      { variant: "error" },
    );
  });

  it.each([
    ["a multiline diagnostic", "Traceback (most recent call last):\n  boom"],
    ["an oversized body", "x".repeat(400)],
  ])("never renders %s from the server", async (_label, raw) => {
    await failWith({
      statusCode: 400,
      detail: raw,
      result: { status: "unreachable", error: raw, last_health_check: "x" },
    });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith("Health check failed", {
      variant: "error",
    });
  });
});
