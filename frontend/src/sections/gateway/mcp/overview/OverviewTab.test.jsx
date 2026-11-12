import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router-dom";

import { renderMcp, DEMO_MCP, demoToolCall } from "../testing/renderMcp";
import { MCP_API } from "../api";
import OverviewTab from "./OverviewTab";

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { id: "gw-1", baseUrl: "http://localhost:28490/v1" },
  }),
}));

const api = vi.hoisted(() => ({ get: vi.fn(), state: {}, admin: true }));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: api.admin }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: api.get } };
});

const STATUS = {
  enabled: true,
  features: { tool_call_explorer: true, virtual_servers: true },
  sessions: 0,
  tools: 6,
  resources: 2,
  prompts: 1,
  servers: [
    {
      server_id: "crm-tools",
      healthy: true,
      tool_count: 2,
      last_refreshed_at: "2026-10-20T17:59:20Z",
    },
    {
      server_id: "support-tools",
      healthy: false,
      tool_count: 0,
      last_error: "initialize: http 404",
    },
  ],
};

beforeEach(() => {
  api.admin = true;
  api.state = {
    mcp: DEMO_MCP,
    status: STATUS,
    calls: {
      next: null,
      results: [
        demoToolCall(),
        demoToolCall({
          id: 45,
          outcome: "denied",
          tool: "crm-tools__find_account",
          upstream_tool: "find_account",
          server_id: "crm-tools",
        }),
      ],
    },
  };
  api.get.mockReset();
  api.get.mockImplementation(async (url) => {
    if (api.state.errors?.includes(url)) throw new Error("Temporarily unavailable");
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: api.state.mcp } } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-status/") {
      return { data: { result: api.state.status } };
    }
    if (url === MCP_API.toolCalls) return { data: api.state.calls };
    throw new Error(`unexpected GET ${url}`);
  });
});

describe("OverviewTab recovery and setup", () => {
  const configUrl = "/agentcc/gateways/gw-1/config/";

  it("retries configuration without replacing a read failure with onboarding", async () => {
    api.state.errors = [configUrl];
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const retry = await screen.findByRole("button", { name: "Retry server configuration" });
    expect(screen.queryByText("Connect your first MCP server")).toBeNull();
    expect(within(screen.getByRole("group", { name: "Servers" })).queryByText("0 of 0")).toBeNull();
    expect(await screen.findByText("delete_customer_data")).toBeInTheDocument();
    api.state.errors = [];
    fireEvent.click(retry);
    expect(await screen.findByText("crm-tools")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry server configuration" })).toBeNull();
  });

  it("retries calls independently and never reports a failed read as zero history", async () => {
    api.state.errors = [MCP_API.toolCalls];
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const retry = await screen.findByRole("button", { name: "Retry tool calls" });
    expect(screen.getByText("crm-tools")).toBeInTheDocument();
    expect(screen.queryByText("No tool call in the last 24 hours.")).toBeNull();
    expect(within(screen.getByRole("group", { name: "Tool calls, last 24 h" })).getByText("Unavailable")).toBeInTheDocument();
    api.state.errors = [];
    fireEvent.click(retry);
    expect(await screen.findByText("delete_customer_data")).toBeInTheDocument();
  });

  it("keeps independent call recovery visible when configuration is genuinely empty", async () => {
    api.state.mcp = { servers: {} };
    api.state.errors = [MCP_API.toolCalls];
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(await screen.findByRole("button", { name: "Retry tool calls" })).toBeInTheDocument();
    expect(screen.getByText("Connect your first MCP server")).toBeInTheDocument();
    expect(screen.queryByText("No tool call in the last 24 hours.")).toBeNull();
  });

  it("labels retained server and call data stale after failed refreshes", async () => {
    const { client } = renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(await screen.findByText("delete_customer_data")).toBeInTheDocument();
    api.state.errors = [configUrl, MCP_API.toolCalls];
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["agentcc-gateway-config"] });
      await client.invalidateQueries({ queryKey: ["agentcc-mcp-tool-calls"] });
    });
    expect(await screen.findByText(/Server configuration is stale/)).toBeInTheDocument();
    expect(screen.getByText(/Tool call history is stale/)).toBeInTheDocument();
    expect(screen.getByText("crm-tools")).toBeInTheDocument();
    expect(screen.getByText("delete_customer_data")).toBeInTheDocument();
  });

  // An absent catalog switch is not off: only a status that says false hides
  // the catalog link, and only once that status has answered.
  it.each([true, false, undefined])("offers catalog navigation unless the switch is off (%s)", async (catalog) => {
    api.state.mcp = { servers: {} };
    api.state.status = { ...STATUS, features: { catalog } };
    const seen = {};
    function Location() {
      const location = useLocation();
      seen.url = `${location.pathname}${location.search}`;
      return null;
    }
    const { client } = renderMcp(<><OverviewTab gatewayId="gw-1" /><Location /></>);
    await screen.findByText("Connect your first MCP server");
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(expect.stringContaining("mcp-status"), expect.anything()));
    await waitFor(() => expect(client.getQueryState(["agentcc-mcp-status", "gw-1"])?.status).toBe("success"));
    const browse = screen.queryByRole("button", { name: "Browse integrations" });
    if (catalog !== false) {
      expect(await screen.findByRole("button", { name: "Browse integrations" })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Browse integrations" }));
      expect(seen.url).toBe("/dashboard/gateway/integrations");
    } else expect(browse).toBeNull();
    expect(api.get.mock.calls.some(([url]) => url === MCP_API.toolCalls)).toBe(false);
  });

  it("keeps deployment-off separate from empty setup and makes no history call", async () => {
    api.state.mcp = { servers: {} };
    api.state.status = { ...STATUS, enabled: false, reason: "gateway_mcp_disabled" };
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(await screen.findByText(/Live MCP operations are unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Browse integrations" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add server" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry tool calls" })).toBeNull();
    expect(api.get.mock.calls.some(([url]) => url === MCP_API.toolCalls)).toBe(false);
  });

  it("gives members guidance rather than an add-server action", async () => {
    api.admin = false;
    api.state.mcp = { servers: {} };
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(await screen.findByText(/Ask an Owner or Admin to add/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add server" })).toBeNull();
  });
});

describe("OverviewTab", () => {
  it("sums up servers, tools, virtual servers and the last day's calls", async () => {
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const servers = await screen.findByRole("group", { name: "Servers" });
    expect(await within(servers).findByText("1 of 2")).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Tools" })).getByText("6"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Virtual servers" })).getByText(
        "1",
      ),
    ).toBeInTheDocument();
    const calls = await screen.findByRole("group", {
      name: "Tool calls, last 24 h",
    });
    expect(await within(calls).findByText("2")).toBeInTheDocument();
    expect(within(calls).getByText("1 not successful")).toBeInTheDocument();
  });

  it("reports no virtual servers without promising future availability", async () => {
    api.state.mcp = { ...DEMO_MCP, virtual_servers: {} };
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const servers = await screen.findByRole("group", {
      name: "Virtual servers",
    });
    expect(await within(servers).findByText("0")).toBeInTheDocument();
    expect(within(servers).getByText("none").textContent).toBe("none");
  });

  it("shows an unhealthy server's last error instead of hiding why", async () => {
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(await screen.findByText("initialize: http 404")).toBeInTheDocument();
    expect(screen.getByText("Unhealthy")).toBeInTheDocument();
  });

  it("gives clients the endpoints to connect to", async () => {
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("http://localhost:28490/mcp"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("http://localhost:28490/mcp/v/support-desk"),
    ).toBeInTheDocument();
  });

  it("counts a full page of calls as a floor, not a total", async () => {
    api.state.calls = {
      next: "http://x/?cursor=abc",
      results: [demoToolCall()],
    };
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const calls = await screen.findByRole("group", {
      name: "Tool calls, last 24 h",
    });
    expect(await within(calls).findByText("1+")).toBeInTheDocument();
  });

  it("starts an org with no servers at adding one", async () => {
    api.state.mcp = { servers: {} };
    api.state.status = { enabled: false, servers: [] };
    api.state.calls = { next: null, results: [] };
    const seen = {};
    const Probe = () => {
      const location = useLocation();
      seen.url = `${location.pathname}${location.search}`;
      return null;
    };
    renderMcp(
      <>
        <OverviewTab gatewayId="gw-1" />
        <Probe />
      </>,
      { route: "/dashboard/gateway/mcp" },
    );
    expect(
      await screen.findByText("Connect your first MCP server"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(seen.url).toBe("/dashboard/gateway/mcp?tab=servers&add=1");
  });
});

describe("OverviewTab long names", () => {
  it("shows a cut tool name and its server line in full on hover", async () => {
    renderMcp(<OverviewTab gatewayId="gw-1" />);
    const tool = await screen.findByText("delete_customer_data");
    expect(tool).toHaveAttribute("title", "delete_customer_data");
    const line = screen.getByText(/^support-tools · /);
    expect(line).toHaveAttribute("title", line.textContent);
  });
});
