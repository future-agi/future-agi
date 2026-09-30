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
  asRequestError,
  useFetchProviderModels,
  useGatewayConfig,
  useReloadConfig,
  useRemoveBudget,
  useRemoveProvider,
  useSetBudget,
  useToggleGuardrail,
  useUpdateConfig,
  useUpdateGuardrail,
  useUpdateProvider,
} from "./useGatewayConfig";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateProvider: (id) => `/gateway/${id}/provider/update`,
      removeProvider: (id) => `/gateway/${id}/provider/remove`,
      toggleGuardrail: (id) => `/gateway/${id}/guardrail/toggle`,
      updateGuardrail: (id) => `/gateway/${id}/guardrail/update`,
      setBudget: (id) => `/gateway/${id}/budget/set`,
      removeBudget: (id) => `/gateway/${id}/budget/remove`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
      reload: (id) => `/gateway/${id}/reload`,
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

  it("finishes only once the provider config has been re-read", async () => {
    const providersWithPrefix = (prefix) => ({
      data: { result: { providers: { openai: { api_path_prefix: prefix } } } },
    });
    get.mockReset();
    get.mockResolvedValueOnce(providersWithPrefix("/openai/v1"));
    post.mockResolvedValue({ data: { result: { action: "updated" } } });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const clientWrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => ({ config: useGatewayConfig("gw-1"), update: useUpdateProvider() }),
      { wrapper: clientWrapper },
    );
    await waitFor(() => expect(result.current.config.data).toBeTruthy());

    // The re-read answers after the save does, as it would over the network.
    get.mockImplementationOnce(
      () =>
        new Promise((resolve) =>
          setTimeout(() => resolve(providersWithPrefix("")), 20),
        ),
    );
    let prefixSeenOnSuccess;
    await act(async () => {
      await new Promise((resolve) =>
        result.current.update.mutate(
          {
            gatewayId: "gw-1",
            name: "openai",
            config: { api_path_prefix: "" },
          },
          {
            // Where AddProviderDialog closes; Edit reads the cache after this.
            onSuccess: () => {
              prefixSeenOnSuccess = client.getQueryData([
                "agentcc-gateway-config",
                "gw-1",
              ]).providers.openai.api_path_prefix;
              resolve();
            },
          },
        ),
      );
    });

    expect(prefixSeenOnSuccess).toBe("");
  });
});

describe("config mutations", () => {
  // Each dialog closes in its mutate() onSuccess, and Edit reads the cached
  // config, so the mutation must not settle before the config is re-read.
  it.each([
    ["useRemoveProvider", useRemoveProvider, { gatewayId: "gw-1", name: "x" }],
    [
      "useToggleGuardrail",
      useToggleGuardrail,
      { gatewayId: "gw-1", name: "pii", enabled: false },
    ],
    [
      "useUpdateGuardrail",
      useUpdateGuardrail,
      { gatewayId: "gw-1", name: "pii", config: {} },
    ],
    [
      "useSetBudget",
      useSetBudget,
      { gatewayId: "gw-1", level: "org", config: {} },
    ],
    ["useRemoveBudget", useRemoveBudget, { gatewayId: "gw-1", level: "org" }],
    ["useUpdateConfig", useUpdateConfig, { gatewayId: "gw-1", config: {} }],
    ["useReloadConfig", useReloadConfig, "gw-1"],
  ])(
    "%s finishes only once the config has been re-read",
    async (_, useHook, variables) => {
      get.mockReset();
      get.mockResolvedValueOnce({ data: { result: { version: 1 } } });
      post.mockResolvedValue({ data: { result: {} } });

      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const clientWrapper = ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const { result } = renderHook(
        () => ({ config: useGatewayConfig("gw-1"), mutation: useHook() }),
        { wrapper: clientWrapper },
      );
      await waitFor(() => expect(result.current.config.data).toBeTruthy());

      get.mockImplementationOnce(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve({ data: { result: { version: 2 } } }), 20),
          ),
      );
      let versionSeenOnSuccess;
      await act(async () => {
        await new Promise((resolve) =>
          result.current.mutation.mutate(variables, {
            onSuccess: () => {
              versionSeenOnSuccess = client.getQueryData([
                "agentcc-gateway-config",
                "gw-1",
              ]).version;
              resolve();
            },
          }),
        );
      });

      expect(versionSeenOnSuccess).toBe(2);
    },
  );
});

describe("useFetchProviderModels", () => {
  beforeEach(() => post.mockReset());

  const fetchWith = (vars) => {
    post.mockResolvedValue({ data: { result: { models: [] } } });
    const { result } = renderHook(() => useFetchProviderModels(), { wrapper });
    return result.current.mutateAsync(vars);
  };

  it("sends the stored provider's prefix so discovery probes the right path", async () => {
    await fetchWith({
      providerName: "perplexity",
      apiFormat: "openai",
      apiPathPrefix: "/openai/v1",
    });

    expect(post.mock.calls[0][1]).toEqual({
      provider_name: "perplexity",
      api_path_prefix: "/openai/v1",
    });
  });

  it("keeps an explicitly empty prefix, which is the headline case", async () => {
    await fetchWith({
      baseUrl: "https://provider.example",
      apiKey: "sk-test",
      apiFormat: "openai",
      apiPathPrefix: "",
    });

    expect(post.mock.calls[0][1]).toEqual({
      base_url: "https://provider.example",
      api_key: "sk-test",
      api_format: "openai",
      api_path_prefix: "",
    });
  });

  it("omits the prefix for a non-OpenAI format, as saving does", async () => {
    await fetchWith({
      baseUrl: "https://api.anthropic.com",
      apiKey: "sk-ant",
      apiFormat: "anthropic",
      apiPathPrefix: "/v1",
    });

    expect(post.mock.calls[0][1]).not.toHaveProperty("api_path_prefix");
  });
});
