import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMCPTools, useMCPResources, useMCPPrompts } from "./useMCPConfig";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    gateway: {
      mcpTools: (id) => `/mcp/${id}/tools`,
      mcpResources: (id) => `/mcp/${id}/resources`,
      mcpPrompts: (id) => `/mcp/${id}/prompts`,
    },
  },
}));
const OFF = {
  enabled: false,
  reason: "gateway_mcp_disabled",
  features: {
    tool_call_explorer: true,
    tool_call_downloads: true,
    virtual_servers: true,
  },
};
const clients = [];
function mount(useHook, status = OFF) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  client.setQueryData(["agentcc-mcp-status", "gw"], status);
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => useHook("gw"), { wrapper }), client };
}
beforeEach(() => {
  get
    .mockReset()
    .mockResolvedValue({ data: { result: [{ name: "alpha__read" }] } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("RL-1009 central live-list boundary, including explorer and virtual callers", () => {
  it.each([
    ["tools", useMCPTools],
    ["resources", useMCPResources],
    ["prompts", useMCPPrompts],
  ])(
    "%s cannot request while core-off even with independent features on, including refetch",
    async (_, useHook) => {
      const { result } = mount(useHook);
      await act(async () => {
        await result.current.refetch();
      });
      expect(get).not.toHaveBeenCalled();
    },
  );
  it.each([
    ["tools", useMCPTools],
    ["resources", useMCPResources],
    ["prompts", useMCPPrompts],
  ])(
    "%s checks latest status on direct refetch after an on-to-off transition",
    async (_, useHook) => {
      const { result, client } = mount(useHook, { enabled: true });
      await waitFor(() =>
        expect(result.current.data).toEqual([{ name: "alpha__read" }]),
      );
      get.mockClear();
      act(() => {
        client.setQueryData(["agentcc-mcp-status", "gw"], OFF);
      });
      await act(async () => {
        await result.current.refetch();
      });
      expect(get).not.toHaveBeenCalled();
    },
  );
  it.each([
    ["tools", useMCPTools],
    ["resources", useMCPResources],
    ["prompts", useMCPPrompts],
  ])(
    "%s retains cached OFF through failed status refresh and manual list refetch",
    async (kind, useHook) => {
      const { result, client } = mount(useHook);
      const cached = [{ name: "alpha__read" }];
      act(() => client.setQueryData([`agentcc-mcp-${kind}`, "gw"], cached));
      await act(async () => {
        await expect(
          client.fetchQuery({
            queryKey: ["agentcc-mcp-status", "gw"],
            queryFn: () => Promise.reject(new Error("Status refresh failed")),
            retry: false,
          }),
        ).rejects.toThrow("Status refresh failed");
      });
      expect(client.getQueryState(["agentcc-mcp-status", "gw"]).status).toBe(
        "error",
      );
      expect(client.getQueryData(["agentcc-mcp-status", "gw"])).toEqual(OFF);
      await act(async () => {
        await result.current.refetch();
      });
      expect(get).not.toHaveBeenCalled();
      expect(client.getQueryData([`agentcc-mcp-${kind}`, "gw"])).toEqual(
        cached,
      );
      await waitFor(() => expect(result.current.data).toEqual(cached));
      // A successful new status, not a refresh error, releases the restriction.
      act(() =>
        client.setQueryData(["agentcc-mcp-status", "gw"], { enabled: true }),
      );
      await act(async () => {
        await result.current.refetch();
      });
      expect(get).toHaveBeenCalledWith(`/mcp/gw/${kind}`);
    },
  );
  it("keeps MCP-on live tools behavior unchanged", async () => {
    const { result } = mount(useMCPTools, { enabled: true });
    await waitFor(() =>
      expect(result.current.data).toEqual([{ name: "alpha__read" }]),
    );
    expect(get).toHaveBeenCalledWith("/mcp/gw/tools");
  });
  it("does not classify org-only off as deployment off", async () => {
    const { result } = mount(useMCPTools, { enabled: false });
    await waitFor(() =>
      expect(result.current.data).toEqual([{ name: "alpha__read" }]),
    );
    expect(get).toHaveBeenCalled();
  });
});
