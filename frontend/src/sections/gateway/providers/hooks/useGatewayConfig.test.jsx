import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { asRequestError, useUpdateProvider } from "./useGatewayConfig";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: {
      updateProvider: (id) => `/gateway/${id}/provider/update`,
      providerCredentials: { fetchModels: "/gateway/provider/models" },
    },
  },
}));

const wrapper = ({ children }) => {
  const client = new QueryClient({
    // Errors are asserted on the mutateAsync promise; the cache-level handler
    // just keeps React Query's own rejection from surfacing as unhandled.
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
};

const save = () => {
  const { result } = renderHook(() => useUpdateProvider(), { wrapper });
  return result.current.mutateAsync({
    gatewayId: "gw-1",
    name: "openai",
    config: {},
  });
};

describe("asRequestError", () => {
  it("turns an axios timeout into an actionable message", () => {
    const timeout = Object.assign(new Error("timeout of 30000ms exceeded"), {
      code: "ECONNABORTED",
    });

    expect(asRequestError(timeout, "Saving the provider").message).toMatch(
      /Saving the provider timed out — the gateway did not respond/,
    );
    expect(asRequestError(timeout, "Saving the provider").cause).toBe(timeout);
  });

  it("leaves ordinary server errors untouched", () => {
    const conflict = new Error("Provider already exists");

    expect(asRequestError(conflict, "Saving the provider")).toBe(conflict);
  });
});

describe("useUpdateProvider", () => {
  beforeEach(() => post.mockReset());

  it("bounds the request so a stalled gateway cannot hang the Save button", async () => {
    post.mockResolvedValue({ data: { result: {} } });

    await save();

    expect(post.mock.calls[0][2]).toEqual({ timeout: 30000 });
  });
});
