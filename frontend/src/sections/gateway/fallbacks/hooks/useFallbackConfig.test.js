import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useFallbackConfig } from "./useFallbackConfig";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      orgConfig: {
        active: "/agentcc/org-configs/active/",
        create: "/agentcc/org-configs/",
      },
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));

const ACTIVE_CONFIG = {
  id: "cfg-3",
  version: 3,
  routing: {
    strategy: "round-robin",
    auto: { preference: "cost" },
    model_fallbacks: { "gpt-4o": ["claude-sonnet-4"], o1: ["gpt-4o"] },
    failover: { enabled: true, max_attempts: 3 },
  },
  guardrails: { rules: [{ name: "pii-detector", enabled: true }] },
  managed_models: { enabled: true },
};

let client;
const wrapper = ({ children }) =>
  React.createElement(QueryClientProvider, { client }, children);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockResolvedValue({ data: { result: ACTIVE_CONFIG } });
  post.mockResolvedValue({
    data: { result: { version: 4, gateway_synced: true } },
  });
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
});

async function renderLoaded() {
  const hook = renderHook(() => useFallbackConfig(), { wrapper });
  await waitFor(() => expect(hook.result.current.isLoading).toBe(false));
  return hook;
}

describe("useFallbackConfig", () => {
  it("exposes the active routing section", async () => {
    const { result } = await renderLoaded();
    expect(result.current.routing).toEqual(ACTIVE_CONFIG.routing);
  });

  it("saveRouting posts only the routing changes to update-config, never a whole org-config version", async () => {
    const { result } = await renderLoaded();
    const draft = {
      ...ACTIVE_CONFIG.routing,
      model_fallbacks: { "gpt-4o": ["claude-sonnet-4"] },
      failover: { enabled: true, max_attempts: 5 },
    };

    await act(async () => {
      await result.current.saveRouting(draft);
    });

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/agentcc/gateways/gw-1/update-config/");
    expect(Object.keys(body)).toEqual(["routing"]);
    // The removed chain is deleted with null: update-config keeps absent keys.
    expect(body).toEqual({
      routing: {
        model_fallbacks: { o1: null },
        failover: { max_attempts: 5 },
      },
    });
    post.mock.calls.forEach(([postedUrl]) =>
      expect(postedUrl).not.toMatch(/org-configs/),
    );
  });

  it("does not write back routing keys another page owns", async () => {
    const { result } = await renderLoaded();
    // A draft opened before the Models page saved routing.auto still carries
    // the old auto and strategy values.
    const staleDraft = {
      ...ACTIVE_CONFIG.routing,
      strategy: "least-latency",
      auto: { preference: "quality" },
      retry: { enabled: true },
    };

    await act(async () => {
      await result.current.saveRouting(staleDraft);
    });

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toEqual({
      routing: { retry: { enabled: true } },
    });
  });

  it("does not post when the draft equals the saved routing", async () => {
    const { result } = await renderLoaded();

    await act(async () => {
      await result.current.saveRouting({ ...ACTIVE_CONFIG.routing });
    });

    expect(post).not.toHaveBeenCalled();
  });

  it("rejects a failed save so the caller keeps its unsaved changes", async () => {
    post.mockRejectedValue({ statusCode: 400, result: "Invalid routing" });
    const { result } = await renderLoaded();

    let caught;
    await act(async () => {
      caught = await result.current
        .saveRouting({ ...ACTIVE_CONFIG.routing, default_model: "gpt-4o" })
        .catch((e) => e);
    });

    expect(caught).toEqual({ statusCode: 400, result: "Invalid routing" });
  });
});
