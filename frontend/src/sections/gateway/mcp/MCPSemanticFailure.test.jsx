import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { endpoints } from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import { renderMcp } from "./testing/renderMcp";
import { readMCPStatus } from "./hooks/readMCPStatus";
import {
  useMCPStatus,
  useMCPTools,
  useMCPResources,
  useMCPPrompts,
} from "./hooks/useMCPConfig";

const api = vi.hoisted(() => ({ adapter: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: (await import("axios")).default.create({
    adapter: (config) => api.adapter(config),
  }),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-semantic", gateway: {} }),
}));

const id = "gw-semantic";
const key = ["agentcc-mcp-status", id];
const gw = endpoints.gateway;
const features = {
  client_oauth: true,
  client_installs: true,
  tool_call_explorer: true,
  tool_call_downloads: true,
  credential_vault: true,
  virtual_servers: true,
};
const OFF = { enabled: false, reason: "gateway_mcp_disabled", features };
const UNREACHABLE = { enabled: false, reason: "gateway_unreachable", features };
const UNKNOWN = { reason: "unknown", features };
const ON = { enabled: true, servers: [], features };
const offTabs = [
  "Overview",
  "Tools",
  "Servers",
  "Resources",
  "Prompts",
  "Guardrails",
  "Playground",
  "Manage MCP clients",
];
const baseTabs = [
  "Overview",
  "Servers",
  "Virtual servers",
  "Tool calls",
  "Credentials",
  "MCP clients",
  "Guardrails",
  "Try a tool",
  "Resources",
  "Prompts",
  "Connect",
];
const lists = [
  ["tools", useMCPTools, gw.mcpTools],
  ["resources", useMCPResources, gw.mcpResources],
  ["prompts", useMCPPrompts, gw.mcpPrompts],
];
const clients = [];
let status;

beforeEach(() => {
  status = OFF;
  api.adapter.mockReset().mockImplementation(async (config) => {
    let result;
    if (config.url === gw.mcpStatus(id)) result = status;
    else if (config.url === gw.config(id))
      result = { mcp: { enabled: true, servers: {} } };
    else if (lists.some(([, , endpoint]) => config.url === endpoint(id)))
      result = [{ name: "alpha__read" }];
    else throw new Error(`Unexpected in-memory request: ${config.url}`);
    return {
      data: { result },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

function openShell() {
  const view = renderMcp(<MCPManagementSection />, {
    route: "/dashboard/gateway/mcp?tab=guardrails",
  });
  clients.push(view.client);
  return view;
}
function tabLabels() {
  return screen.getAllByRole("tab").map((tab) => tab.textContent);
}
function listRequests(endpoint) {
  return api.adapter.mock.calls.filter(
    ([config]) => config.url === endpoint(id),
  );
}
async function mountList(useList) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  // Seed OFF via the exact status reader and an HTTP-200 in-memory adapter.
  await client.fetchQuery({
    queryKey: key,
    queryFn: ({ signal }) => readMCPStatus(id, signal),
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    () => {
      const query = useMCPStatus(id);
      // Track data during render, like the shell. Reading it only in assertions
      // does not subscribe this observer once the list's OFF snapshot stays put.
      return {
        status: { data: query.data, refetch: query.refetch },
        list: useList(id),
      };
    },
    { wrapper },
  );
  return { ...view, client };
}

// No runtime/status/tab functions are mocked: these use the shipped shell,
// reader, query options and list queryFns with a real QueryClient.
describe("SOL4-1009-1 semantic status failures", () => {
  it.each([UNREACHABLE, UNKNOWN])(
    "keeps the off shell after an HTTP-200 $reason refresh",
    async (degraded) => {
      const { client, unmount } = openShell();
      await waitFor(() => expect(tabLabels()).toEqual(offTabs));
      status = degraded;
      await act(async () => {
        await client.refetchQueries({ queryKey: key });
      });
      expect(client.getQueryState(key).status).toBe("success");
      expect(client.getQueryData(key).reason).toBe(degraded.reason);
      // Query notifications are scheduled after the HTTP promise settles.
      // Wait for the new verdict, not the still-rendered initial OFF tabs.
      await waitFor(() =>
        expect(
          screen.queryByText("MCP is turned off on this gateway"),
        ).toBeNull(),
      );
      expect(tabLabels()).toEqual(offTabs);
      expect(
        screen.getByRole("button", { name: "Reload config" }),
      ).toBeEnabled();
      if (degraded === UNREACHABLE)
        expect(
          screen.getByText("The gateway could not be reached"),
        ).toBeInTheDocument();
      expect(api.adapter.mock.calls.map(([config]) => config.url)).toEqual(
        expect.arrayContaining([gw.config(id), gw.mcpStatus(id)]),
      );
      expect(
        api.adapter.mock.calls.every(([config]) =>
          [gw.config(id), gw.mcpStatus(id)].includes(config.url),
        ),
      ).toBe(true);
      // The restriction must outlive the shell observer, not live in a ref.
      unmount();
      expect(client.getQueryData(key).reason).toBe(degraded.reason);
    },
  );

  it.each(lists)(
    "keeps automatic %s reads suppressed after an HTTP-200 unreachable refresh",
    async (kind, useList, endpoint) => {
      const { result, client } = await mountList(useList);
      expect(listRequests(endpoint)).toEqual([]);
      status = UNREACHABLE;
      await act(async () => {
        await result.current.status.refetch();
      });
      await waitFor(() =>
        expect(result.current.status.data.reason).toBe("gateway_unreachable"),
      );
      expect(listRequests(endpoint)).toEqual([]);
      expect(
        client.getQueryState([`agentcc-mcp-${kind}`, id]).fetchStatus,
      ).toBe("idle");
    },
  );

  it.each(lists)(
    "guards a captured manual %s refetch after an HTTP-200 unreachable refresh",
    async (kind, useList, endpoint) => {
      const { result, client } = await mountList(useList);
      const cached = [{ name: "previously_loaded" }];
      act(() => client.setQueryData([`agentcc-mcp-${kind}`, id], cached));
      const refetch = result.current.list.refetch;
      status = UNREACHABLE;
      let manualRequests;
      await act(async () => {
        await result.current.status.refetch();
        const before = listRequests(endpoint).length;
        await refetch();
        manualRequests = listRequests(endpoint).length - before;
      });
      expect(manualRequests).toBe(0);
      expect(client.getQueryData([`agentcc-mcp-${kind}`, id])).toEqual(cached);
    },
  );

  it.each([UNREACHABLE, UNKNOWN])(
    "keeps the base shell for cold $reason with no authoritative OFF",
    async (degraded) => {
      status = degraded;
      const { client } = openShell();
      await waitFor(() =>
        expect(client.getQueryData(key)?.reason).toBe(degraded.reason),
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /Add stored settings/ }),
        ).toBeEnabled(),
      );
      expect(tabLabels()).toEqual(baseTabs);
      expect(screen.getByRole("tab", { name: "Resources" })).toBeDisabled();
      expect(screen.getByRole("tab", { name: "Prompts" })).toBeDisabled();
      expect(
        screen.queryByRole("tab", { name: "Manage MCP clients" }),
      ).toBeNull();
    },
  );

  it("releases the retained restriction only after a reliable ON response", async () => {
    const { result } = await mountList(useMCPTools);
    status = UNREACHABLE;
    await act(async () => {
      await result.current.status.refetch();
    });
    expect(listRequests(gw.mcpTools)).toEqual([]);
    status = UNKNOWN;
    await act(async () => {
      await result.current.status.refetch();
    });
    expect(listRequests(gw.mcpTools)).toEqual([]);
    status = ON;
    await act(async () => {
      await result.current.status.refetch();
    });
    await waitFor(() =>
      expect(result.current.list.data).toEqual([{ name: "alpha__read" }]),
    );
    expect(listRequests(gw.mcpTools).length).toBeGreaterThan(0);
  });
});
