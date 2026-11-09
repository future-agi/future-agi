import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";
import { creditKeys } from "src/sections/settings/BillingV2/gateway-credits/api";
import { useKeyProviderOptions } from "../../keys/hooks/useKeyProviderOptions";
import {
  MANAGED_MODELS_QUERY_KEY,
  MANAGED_CATALOG_QUERY_KEY,
  useManagedModelsStatus,
  useSetManagedModelsEnabled,
} from "./useManagedModels";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    gateway: {
      updateConfig: (id) => `/gateway/${id}/config/update`,
      managedModels: "/managed-models",
      managedCredits: "/managed-credits",
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: true, isLoading: false }),
}));

vi.mock("../../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useGatewayConfig: () => ({ data: { providers: { own: {} } } }),
}));
vi.mock("../../providers/hooks/useOrgConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useOrgConfig: () => ({ data: null }),
}));

let client;
let enabled;
const response = (result) => ({ data: { status: true, result } });
const summary = () =>
  response({ enabled, billing_available: true, available_usd: "25.00" });
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
const useBillingAndMutation = () => ({
  billing: useCentralBilling(),
  status: useManagedModelsStatus(),
  mutation: useSetManagedModelsEnabled(),
});

beforeEach(() => {
  enabled = true;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  api.get.mockReset().mockImplementation((url) => {
    if (url === "/managed-credits") return Promise.resolve(summary());
    if (url === "/managed-models") {
      return Promise.resolve(response({ available: true, enabled }));
    }
    throw new Error(`Unexpected fixture request: ${url}`);
  });
  api.post.mockReset().mockImplementation((_url, body) => {
    enabled = body.managed_models.enabled;
    return Promise.resolve(response({ version: 4, gateway_synced: true }));
  });
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe("SOL-R1-01 managed enablement refreshes credit consumers", () => {
  it.each([false, true])(
    "refreshes the mounted wallet gate after saving enabled=%s",
    async (nextEnabled) => {
      enabled = !nextEnabled;
      const { result } = renderHook(useBillingAndMutation, { wrapper });
      await waitFor(() => expect(result.current.status.isSuccess).toBe(true));
      await waitFor(() =>
        expect(result.current.billing.creditsAvailable).toBe(!nextEnabled),
      );
      await waitFor(() => expect(result.current.billing.isLoading).toBe(false));
      await act(async () => {
        await result.current.mutation.mutateAsync({
          gatewayId: "gw-1",
          enabled: nextEnabled,
        });
      });
      await waitFor(() =>
        expect(result.current.status.data.enabled).toBe(nextEnabled),
      );
      await waitFor(() =>
        expect(result.current.billing.creditsAvailable).toBe(nextEnabled),
      );
      expect(result.current.billing.visible).toBe(true);
      expect(result.current.billing.credits).toEqual(
        nextEnabled ? expect.objectContaining({ enabled: true }) : null,
      );
      expect(
        api.get.mock.calls.filter(([url]) => url === "/managed-credits"),
      ).toHaveLength(2);
    },
  );

  it("invalidates inactive credit summaries, settings, entries and statements", async () => {
    const keys = [
      creditKeys.summary,
      creditKeys.settings,
      creditKeys.entries("all"),
      creditKeys.statement("2026-11"),
    ];
    keys.forEach((key) => client.setQueryData(key, summary()));
    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });
    await act(async () => {
      await result.current.mutateAsync({ gatewayId: "gw-1", enabled: false });
    });
    keys.forEach((key) => {
      expect(client.getQueryState(key).isInvalidated).toBe(true);
    });
  });

  it("closes cached wallet controls even if the summary refresh fails after disablement", async () => {
    const { result } = renderHook(useBillingAndMutation, { wrapper });
    await waitFor(() =>
      expect(result.current.billing.creditsAvailable).toBe(true),
    );
    const originalGet = api.get.getMockImplementation();
    api.get.mockImplementation((url) =>
      url === "/managed-credits"
        ? Promise.reject(new Error("Fixture summary refresh failed"))
        : originalGet(url),
    );
    await act(async () => {
      await result.current.mutation.mutateAsync({
        gatewayId: "gw-1",
        enabled: false,
      });
    });
    await waitFor(() =>
      expect(client.getQueryState(creditKeys.summary).status).toBe("error"),
    );
    expect(result.current.billing.creditsAvailable).toBe(false);
    expect(result.current.billing.credits).toBeNull();
    expect(result.current.billing.visible).toBe(true);
  });

  it("closes an inactive summary before a later page can reuse it", async () => {
    client.setQueryData(creditKeys.summary, summary());
    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });
    await act(async () => {
      await result.current.mutateAsync({ gatewayId: "gw-1", enabled: false });
    });
    expect(client.getQueryData(creditKeys.summary).data.result).toEqual({
      enabled: false,
      billing_available: true,
      available_usd: "25.00",
    });
    expect(api.get).not.toHaveBeenCalled();
  });

  it("SOL-R2-01 removes the managed provider offer when the status refresh fails after disablement", async () => {
    client.setQueryData([MANAGED_CATALOG_QUERY_KEY], {
      models: [{ provider: "managed-provider" }],
    });
    const { result } = renderHook(
      () => ({
        ...useBillingAndMutation(),
        keyProviders: useKeyProviderOptions("gw-1"),
      }),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.keyProviders.options).toEqual([
        "own",
        "managed-provider",
      ]),
    );
    await waitFor(() =>
      expect(result.current.billing.creditsAvailable).toBe(true),
    );
    const originalGet = api.get.getMockImplementation();
    api.get.mockImplementation((url) =>
      url === "/managed-models"
        ? Promise.reject(new Error("Fixture managed status refresh failed"))
        : originalGet(url),
    );
    await act(async () => {
      await result.current.mutation.mutateAsync({
        gatewayId: "gw-1",
        enabled: false,
      });
    });
    await waitFor(() => expect(result.current.status.isError).toBe(true));
    expect(enabled).toBe(false);
    expect(result.current.billing.creditsAvailable).toBe(false);
    expect(result.current.keyProviders.options).toEqual(["own"]);
    expect(result.current.status.data).toEqual({
      available: true,
      enabled: false,
    });
  });

  it("SOL-R2-01 closes inactive managed status without changing its other fields", async () => {
    const previous = {
      available: true,
      enabled: true,
      balance_usd: 25,
      markup_percent: 5,
    };
    client.setQueryData([MANAGED_MODELS_QUERY_KEY], previous);
    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });
    await act(async () => {
      await result.current.mutateAsync({ gatewayId: "gw-1", enabled: false });
    });
    expect(client.getQueryData([MANAGED_MODELS_QUERY_KEY])).toEqual({
      ...previous,
      enabled: false,
    });
    expect(api.get).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "does not invent unread status when saving enabled=%s",
    async (nextEnabled) => {
      const { result } = renderHook(() => useSetManagedModelsEnabled(), {
        wrapper,
      });
      await act(async () => {
        await result.current.mutateAsync({
          gatewayId: "gw-1",
          enabled: nextEnabled,
        });
      });
      expect(client.getQueryData([MANAGED_MODELS_QUERY_KEY])).toBeUndefined();
    },
  );

  it("does not optimistically enable cached managed status", async () => {
    const previous = { available: true, enabled: false };
    client.setQueryData([MANAGED_MODELS_QUERY_KEY], previous);
    const { result } = renderHook(() => useSetManagedModelsEnabled(), {
      wrapper,
    });
    await act(async () => {
      await result.current.mutateAsync({ gatewayId: "gw-1", enabled: true });
    });
    expect(client.getQueryData([MANAGED_MODELS_QUERY_KEY])).toEqual(previous);
  });

  it("keeps the enabled wallet when the disable request is rejected", async () => {
    api.post.mockRejectedValue(new Error("Fixture write rejected"));
    const { result } = renderHook(useBillingAndMutation, { wrapper });
    await waitFor(() =>
      expect(result.current.billing.creditsAvailable).toBe(true),
    );
    await act(async () => {
      await expect(
        result.current.mutation.mutateAsync({
          gatewayId: "gw-1",
          enabled: false,
        }),
      ).rejects.toThrow();
    });
    expect(result.current.billing.creditsAvailable).toBe(true);
    expect(result.current.status.data.enabled).toBe(true);
    expect(
      api.get.mock.calls.filter(([url]) => url === "/managed-credits"),
    ).toHaveLength(1);
  });
});
