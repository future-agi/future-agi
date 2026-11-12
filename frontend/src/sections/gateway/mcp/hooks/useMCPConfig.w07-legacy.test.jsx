import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useMCPStatus,
  useMCPTools,
  useTestMCPTool,
  useUpdateMCPServer,
  useRemoveMCPServer,
} from "./useMCPConfig";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    gateway: {
      mcpStatus: () => "/status/",
      mcpTools: () => "/tools/",
      testMcpTool: () => "/test/",
      updateMcpServer: () => "/update/",
      removeMcpServer: () => "/remove/",
    },
  },
}));
let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.get.mockImplementation(async (path) => ({
    data: {
      result:
        path === "/status/"
          ? {
              enabled: true,
              features: {
                catalog: false,
                credential_vault: false,
                virtual_servers: false,
              },
            }
          : [{ name: "static_read", server_id: "static" }],
    },
  }));
  api.post.mockResolvedValue({ data: { result: { gateway_synced: true } } });
});
afterEach(() => {
  cleanup();
  client.clear();
});
it("all W07 switches OFF preserve static-server mutations, discovery and playground calls", async () => {
  const { result } = renderHook(
    () => ({
      status: useMCPStatus("gw"),
      tools: useMCPTools("gw"),
      run: useTestMCPTool(),
      update: useUpdateMCPServer(),
      remove: useRemoveMCPServer(),
    }),
    { wrapper },
  );
  await waitFor(() =>
    expect(result.current.status.data?.features.catalog).toBe(false),
  );
  await waitFor(() => expect(result.current.tools.data).toHaveLength(1));
  for (const auth of [
    { type: "none" },
    { type: "bearer", token: "fixture-token" },
    { type: "api_key", header: "X-Key", key: "fixture-key" },
  ]) {
    await act(async () => {
      await result.current.update.mutateAsync({
        gatewayId: "gw",
        serverId: "static",
        config: { url: "https://fixture.invalid/mcp", auth },
      });
    });
  }
  await act(async () => {
    await result.current.run.mutateAsync({
      gatewayId: "gw",
      name: "static_read",
      arguments: {},
    });
    await result.current.remove.mutateAsync({
      gatewayId: "gw",
      serverId: "static",
    });
  });
  expect(api.post.mock.calls.map(([path]) => path)).toEqual([
    "/update/",
    "/update/",
    "/update/",
    "/test/",
    "/remove/",
  ]);
});
