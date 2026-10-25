// A save Django stored but the gateway did not apply.
// Django answers 200 with gateway_synced:false and gateway_warning when the
// push to the gateway fails. Every config write in useGatewayConfig.js must
// surface that as "Saved, but the gateway did not apply it: <warning>" (a
// warning, never a success), refresh the config it changed, and keep the
// caller's onSuccess (its success toast) and its generic onError ("Failed
// to ...", untrue: the change was saved) from running.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { renderHook, waitFor } from "src/utils/test-utils";
import {
  SavedButNotAppliedError,
  isSavedButNotApplied,
  useReloadConfig,
  useRemoveBudget,
  useRemoveProvider,
  useSetBudget,
  useToggleGuardrail,
  useUpdateConfig,
  useUpdateGuardrail,
  useUpdateProvider,
} from "./useGatewayConfig";

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars, globalErrors } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
  globalErrors: [],
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        providers: p("providers"),
        updateProvider: p("update-provider"),
        removeProvider: p("remove-provider"),
        toggleGuardrail: p("toggle-guardrail"),
        updateGuardrail: p("update-guardrail"),
        setBudget: p("set-budget"),
        removeBudget: p("remove-budget"),
        updateConfig: p("update-config"),
        reload: p("reload"),
      },
    },
  };
});

const unsynced = (extra) => ({
  data: {
    status: true,
    result: { ...extra, gateway_synced: false, gateway_warning: WARNING },
  },
});
const synced = (extra) => ({
  data: { status: true, result: { ...extra, gateway_synced: true } },
});

let client;
function wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const HOOKS = [
  [
    "useUpdateProvider",
    useUpdateProvider,
    { gatewayId: "gw-1", name: "openai", config: { api_key: "k" } },
  ],
  [
    "useRemoveProvider",
    useRemoveProvider,
    { gatewayId: "gw-1", name: "openai" },
  ],
  [
    "useToggleGuardrail",
    useToggleGuardrail,
    { gatewayId: "gw-1", name: "pii", enabled: true },
  ],
  [
    "useUpdateGuardrail",
    useUpdateGuardrail,
    { gatewayId: "gw-1", name: "pii", config: { action: "block" } },
  ],
  [
    "useSetBudget",
    useSetBudget,
    { gatewayId: "gw-1", level: "org", config: { limit: 10 } },
  ],
  ["useRemoveBudget", useRemoveBudget, { gatewayId: "gw-1", level: "org" }],
  [
    "useUpdateConfig",
    useUpdateConfig,
    { gatewayId: "gw-1", config: { alerting: { rules: [] } } },
  ],
  ["useReloadConfig", useReloadConfig, "gw-1"],
];

describe("useGatewayConfig writes on gateway_synced:false", () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    get.mockResolvedValue({ data: { status: true, result: { version: 8 } } });
    snackbars.length = 0;
    globalErrors.length = 0;
    client = new QueryClient({
      // The app's MutationCache onError (utils/queryErrorHandler) toasts an
      // error that carries `result`; record what it would see.
      mutationCache: new MutationCache({
        onError: (error) => globalErrors.push(error),
      }),
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
  });

  it.each(HOOKS)(
    "%s: warns once, never calls the caller's onSuccess or generic onError, refreshes the config",
    async (_name, useHook, vars) => {
      // A config read is cached, so the refresh after the save is observable.
      client.setQueryData(["agentcc-gateway-config", "gw-1"], { version: 7 });
      post.mockResolvedValueOnce(unsynced({ version: 8 }));
      const { result } = renderHook(() => useHook(), { wrapper });
      const onSuccess = vi.fn();
      const onError = vi.fn();
      const onSettled = vi.fn();
      result.current.mutate(vars, { onSuccess, onError, onSettled });

      await waitFor(() => expect(result.current.isError).toBe(true));
      await waitFor(() => expect(onSettled).toHaveBeenCalled());
      expect(onSuccess).not.toHaveBeenCalled();
      expect(onError).not.toHaveBeenCalled();
      expect(snackbars).toEqual([
        { message: SENTENCE, options: { variant: "warning" } },
      ]);
      // Dialogs render mutation.error.message in their error Alert.
      expect(result.current.error).toBeInstanceOf(SavedButNotAppliedError);
      expect(result.current.error.message).toBe(SENTENCE);
      expect(isSavedButNotApplied(result.current.error)).toBe(true);
      // The app-wide error toast stays quiet: the error carries no `result`.
      expect(globalErrors).toHaveLength(1);
      expect(globalErrors[0].result).toBeUndefined();
      // The config the save changed was re-read.
      expect(
        client.getQueryState(["agentcc-gateway-config", "gw-1"]).isInvalidated,
      ).toBe(true);
    },
  );

  it.each(HOOKS)(
    "%s: mutateAsync rejects with the warning",
    async (_name, useHook, vars) => {
      post.mockResolvedValueOnce(unsynced({ version: 8 }));
      const { result } = renderHook(() => useHook(), { wrapper });
      await expect(result.current.mutateAsync(vars)).rejects.toMatchObject({
        message: SENTENCE,
        savedButNotApplied: true,
      });
    },
  );

  it.each(HOOKS)(
    "%s: a synced save still reaches the caller's onSuccess, with no hook toast",
    async (_name, useHook, vars) => {
      post.mockResolvedValueOnce(synced({ version: 8 }));
      const { result } = renderHook(() => useHook(), { wrapper });
      const onSuccess = vi.fn();
      const onError = vi.fn();
      result.current.mutate(vars, { onSuccess, onError });
      await waitFor(() => expect(onSuccess).toHaveBeenCalled());
      expect(onSuccess.mock.calls[0][0]).toMatchObject({
        gateway_synced: true,
      });
      expect(onError).not.toHaveBeenCalled();
      expect(snackbars).toEqual([]);
    },
  );

  it.each(HOOKS)(
    "%s: a failed request still reaches the caller's onError",
    async (_name, useHook, vars) => {
      post.mockRejectedValueOnce({ statusCode: 400, result: "Bad config" });
      const { result } = renderHook(() => useHook(), { wrapper });
      const onSuccess = vi.fn();
      const onError = vi.fn();
      result.current.mutate(vars, { onSuccess, onError });
      await waitFor(() => expect(onError).toHaveBeenCalled());
      expect(onError.mock.calls[0][0]).toMatchObject({ result: "Bad config" });
      expect(onSuccess).not.toHaveBeenCalled();
      expect(snackbars).toEqual([]);
    },
  );

  it("a result with no gateway_synced field stays a success", async () => {
    post.mockResolvedValueOnce({
      data: { status: true, result: { version: 3 } },
    });
    const { result } = renderHook(() => useUpdateConfig(), { wrapper });
    const onSuccess = vi.fn();
    result.current.mutate(
      { gatewayId: "gw-1", config: { cache: { enabled: true } } },
      { onSuccess },
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(snackbars).toEqual([]);
  });

  it("names the gateway in the sentence even when the server sends no warning", async () => {
    post.mockResolvedValueOnce({
      data: { status: true, result: { gateway_synced: false } },
    });
    const { result } = renderHook(() => useReloadConfig(), { wrapper });
    result.current.mutate("gw-1");
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error.message).toBe(
      "Saved, but the gateway did not apply it: the gateway did not confirm the change.",
    );
  });
});
