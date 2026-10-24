// MCP saves Django stored but the gateway did not apply.
// update-mcp-server, remove-mcp-server and update-mcp-guardrails answer 200
// with gateway_synced:false and gateway_warning when the push fails. The
// hooks surface "Saved, but the gateway did not apply it: <warning>" as a
// warning, never a success, refresh the MCP status and config, and keep the
// caller's success toast and its generic "Failed to ..." toast from running.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { renderHook, waitFor } from "src/utils/test-utils";
import {
  useRemoveMCPServer,
  useTestMCPTool,
  useUpdateMCPGuardrails,
  useUpdateMCPServer,
} from "./useMCPConfig";

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
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
        updateMcpServer: p("update-mcp-server"),
        removeMcpServer: p("remove-mcp-server"),
        updateMcpGuardrails: p("update-mcp-guardrails"),
        testMcpTool: p("test-mcp-tool"),
        mcpStatus: p("mcp-status"),
        mcpTools: p("mcp-tools"),
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

let client;
function wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const HOOKS = [
  [
    "useUpdateMCPServer",
    useUpdateMCPServer,
    {
      gatewayId: "gw-1",
      serverId: "github",
      config: { transport: "http", url: "http://mcp:8080" },
    },
  ],
  [
    "useRemoveMCPServer",
    useRemoveMCPServer,
    { gatewayId: "gw-1", serverId: "github" },
  ],
  [
    "useUpdateMCPGuardrails",
    useUpdateMCPGuardrails,
    { gatewayId: "gw-1", config: { enabled: true, blocked_tools: ["x"] } },
  ],
];

describe("MCP writes on gateway_synced:false", () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    get.mockResolvedValue({ data: { status: true, result: {} } });
    snackbars.length = 0;
    client = new QueryClient({
      mutationCache: new MutationCache({ onError: () => {} }),
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
  });

  it.each(HOOKS)(
    "%s: one warning, no caller onSuccess or onError, status and config refreshed",
    async (_name, useHook, vars) => {
      client.setQueryData(["agentcc-gateway-config", "gw-1"], { mcp: {} });
      client.setQueryData(["agentcc-mcp-status", "gw-1"], { enabled: true });
      post.mockResolvedValueOnce(unsynced({ server: "github", version: 2 }));
      const { result } = renderHook(() => useHook(), { wrapper });
      const onSuccess = vi.fn();
      const onError = vi.fn();
      result.current.mutate(vars, { onSuccess, onError });

      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(onSuccess).not.toHaveBeenCalled();
      expect(onError).not.toHaveBeenCalled();
      expect(snackbars).toEqual([
        { message: SENTENCE, options: { variant: "warning" } },
      ]);
      expect(result.current.error.message).toBe(SENTENCE);
      expect(
        client.getQueryState(["agentcc-gateway-config", "gw-1"]).isInvalidated,
      ).toBe(true);
      expect(
        client.getQueryState(["agentcc-mcp-status", "gw-1"]).isInvalidated,
      ).toBe(true);
    },
  );

  it.each(HOOKS)(
    "%s: a synced save reaches the caller's onSuccess",
    async (_name, useHook, vars) => {
      post.mockResolvedValueOnce({
        data: { status: true, result: { gateway_synced: true, version: 2 } },
      });
      const { result } = renderHook(() => useHook(), { wrapper });
      const onSuccess = vi.fn();
      result.current.mutate(vars, { onSuccess });
      await waitFor(() => expect(onSuccess).toHaveBeenCalled());
      expect(snackbars).toEqual([]);
    },
  );

  it.each(HOOKS)(
    "%s: a refused request reaches the caller's onError",
    async (_name, useHook, vars) => {
      post.mockRejectedValueOnce({ statusCode: 400, result: "bad server" });
      const { result } = renderHook(() => useHook(), { wrapper });
      const onError = vi.fn();
      result.current.mutate(vars, { onError });
      await waitFor(() => expect(onError).toHaveBeenCalled());
      expect(snackbars).toEqual([]);
    },
  );

  it("Test Tool is not a config write and is left alone", async () => {
    post.mockResolvedValueOnce({
      data: { status: true, result: { content: [], gateway_synced: false } },
    });
    const { result } = renderHook(() => useTestMCPTool(), { wrapper });
    const onSuccess = vi.fn();
    result.current.mutate(
      { gatewayId: "gw-1", name: "search", arguments: {} },
      { onSuccess },
    );
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(snackbars).toEqual([]);
  });
});
