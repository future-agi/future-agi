import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, waitFor } from "@testing-library/react";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import {
  useRemoveMCPServer,
  useUpdateMCPGuardrails,
  useUpdateMCPServer,
} from "./useMCPConfig";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateMcpServer: (id) => `/gateway/${id}/mcp/server/update`,
      removeMcpServer: (id) => `/gateway/${id}/mcp/server/remove`,
      updateMcpGuardrails: (id) => `/gateway/${id}/mcp/guardrails/update`,
    },
  },
}));

describe("MCP config mutations", () => {
  // AddMCPServerDialog closes in its mutate() onSuccess, and Edit in the
  // servers tab reads config.mcp from the cached gateway config, so the
  // mutation must not settle before that config is re-read.
  it.each([
    [
      "useUpdateMCPServer",
      useUpdateMCPServer,
      { gatewayId: "gw-1", serverId: "search", config: {} },
    ],
    [
      "useRemoveMCPServer",
      useRemoveMCPServer,
      { gatewayId: "gw-1", serverId: "search" },
    ],
    [
      "useUpdateMCPGuardrails",
      useUpdateMCPGuardrails,
      { gatewayId: "gw-1", config: {} },
    ],
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
