import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
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

const api = vi.hoisted(() => ({ get: vi.fn(), state: {} }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: api.get } };
});

const STATUS = {
  enabled: true,
  features: { tool_call_explorer: true },
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
