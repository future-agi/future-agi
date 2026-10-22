import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, waitFor } from "@testing-library/react";
import {
  saveErrorMessage,
  useManagedCatalog,
  useManagedModelsStatus,
  useSaveAutoRouting,
  useSetManagedModelsEnabled,
} from "./useManagedModels";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
      managedModels: "/managed-models",
      managedModelsCatalog: "/managed-models/catalog",
    },
  },
}));

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
});

describe("useManagedModelsStatus", () => {
  it("reads the org's managed-model status, unwrapped from result", async () => {
    const status = {
      available: true,
      enabled: false,
      balance_usd: 12.5,
      markup_percent: 5,
    };
    get.mockResolvedValue({ data: { status: true, result: status } });

    const { result } = renderHook(() => useManagedModelsStatus(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(status);
    expect(get).toHaveBeenCalledWith("/managed-models", expect.any(Object));
  });
});

describe("useManagedCatalog", () => {
  it("reads the catalog", async () => {
    const catalog = { markup_percent: 5, providers: [], models: [{ id: "a" }] };
    get.mockResolvedValue({ data: { status: true, result: catalog } });

    const { result } = renderHook(() => useManagedCatalog(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(catalog);
    expect(get).toHaveBeenCalledWith(
      "/managed-models/catalog",
      expect.any(Object),
    );
  });

  it("does not ask for a catalog the deployment does not have", () => {
    renderHook(() => useManagedCatalog({ enabled: false }), { wrapper });
    expect(get).not.toHaveBeenCalled();
  });
});

describe("useSetManagedModelsEnabled", () => {
  it("patches managed_models.enabled and re-reads the status", async () => {
    post.mockResolvedValue({
      data: { status: true, result: { version: 4, gateway_synced: true } },
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });
    let saved;
    await act(async () => {
      saved = await result.current.mutateAsync({
        gatewayId: "gw-1",
        enabled: true,
      });
    });

    expect(post).toHaveBeenCalledWith(
      "/gateway/gw-1/config/update",
      { managed_models: { enabled: true } },
      expect.objectContaining({ timeout: 30000 }),
    );
    expect(saved).toEqual({ version: 4, gateway_synced: true });
    const keys = invalidate.mock.calls.map(([arg]) => arg.queryKey[0]);
    expect(keys).toEqual(
      expect.arrayContaining([
        "agentcc-managed-models",
        "agentcc-gateway-config",
      ]),
    );
  });

  it("turns a timeout into a message that says what timed out", async () => {
    post.mockRejectedValue(
      Object.assign(new Error("timeout of 30000ms exceeded"), {
        code: "ECONNABORTED",
      }),
    );
    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });

    let caught;
    await act(async () => {
      caught = await result.current
        .mutateAsync({ gatewayId: "gw-1", enabled: false })
        .catch((e) => e);
    });

    expect(saveErrorMessage(caught, "fallback")).toMatch(
      /^Saving managed models timed out/,
    );
  });
});

describe("useSaveAutoRouting", () => {
  it("patches routing.auto with the given fields", async () => {
    post.mockResolvedValue({ data: { status: true, result: { version: 5 } } });
    const { result } = renderHook(() => useSaveAutoRouting(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        gatewayId: "gw-1",
        auto: { preference: "cost", candidates: null },
      });
    });

    expect(post).toHaveBeenCalledWith(
      "/gateway/gw-1/config/update",
      { routing: { auto: { preference: "cost", candidates: null } } },
      expect.any(Object),
    );
  });
});

describe("saveErrorMessage", () => {
  it("falls back for a server error with no safe message", () => {
    expect(saveErrorMessage({ statusCode: 500 }, "Failed to save")).toBe(
      "Failed to save",
    );
  });

  it("shows a validation message from the server", () => {
    expect(
      saveErrorMessage(
        // The axios interceptor rejects with the response body spread in.
        { statusCode: 400, result: "Unknown config fields: x" },
        "Failed to save",
      ),
    ).toBe("Unknown config fields: x");
  });
});
