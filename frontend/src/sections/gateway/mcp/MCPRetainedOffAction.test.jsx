import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { endpoints } from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import { renderMcp } from "./testing/renderMcp";

const api = vi.hoisted(() => ({ adapter: vi.fn(), role: "Owner" }));
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
  useGatewayAdmin: () => ({ canEdit: ["Owner", "Admin"].includes(api.role) }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-retained-action", gateway: {} }),
}));

const id = "gw-retained-action";
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
const UNKNOWN = { reason: "unknown", features };
const MALFORMED = { enabled: "false", features };
// A boolean/no-reason response proves the runtime answered; the org is off.
const AVAILABLE = { enabled: false, servers: [], features };
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
const savedCases = [
  { saved: "disabled", config: { enabled: false, servers: {} } },
  { saved: "absent", config: { servers: {} } },
];
const adminCases = ["Owner", "Admin"].flatMap((role) =>
  savedCases.map((saved) => ({ role, ...saved })),
);
const clients = [];
let status;
let mcpConfig;

beforeEach(() => {
  status = OFF;
  mcpConfig = {};
  api.role = "Owner";
  api.adapter.mockReset().mockImplementation(async (config) => {
    let result;
    if (config.url === gw.mcpStatus(id)) result = status;
    else if (config.url === gw.config(id)) result = { mcp: mcpConfig };
    else if (config.url === gw.updateConfig(id) && config.method === "post") {
      mcpConfig = { ...mcpConfig, enabled: true };
      status = { enabled: true, servers: [], features };
      result = { gateway_synced: true };
    } else throw new Error(`Unexpected in-memory request: ${config.url}`);
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

function openShell(role, config) {
  api.role = role;
  mcpConfig = config;
  // Real shell, status/verdict helpers, mutation and QueryClient; no network.
  const view = renderMcp(<MCPManagementSection />, {
    route: "/dashboard/gateway/mcp?tab=guardrails",
  });
  clients.push(view.client);
  return view;
}
function tabLabels() {
  return screen.getAllByRole("tab").map((tab) => tab.textContent);
}
function writes() {
  return api.adapter.mock.calls.filter(([config]) => config.method !== "get");
}
async function refresh(client, next) {
  status = next;
  await act(async () => {
    await client.refetchQueries({ queryKey: key });
  });
  await waitFor(() =>
    expect(screen.queryByText("MCP is turned off on this gateway")).toBeNull(),
  );
}
async function expectWorkingOptIn() {
  const button = await screen.findByRole("button", { name: "Turn on MCP" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(writes()).toHaveLength(1));
  const [request] = writes()[0];
  expect(request.url).toBe(gw.updateConfig(id));
  expect(JSON.parse(request.data)).toEqual({ mcp: { enabled: true } });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull(),
  );
}

describe("SOLB5-1009-1 retained-OFF banner actions", () => {
  it.each(
    adminCases.flatMap((row) => [
      { ...row, state: "unknown", degraded: UNKNOWN },
      { ...row, state: "malformed", degraded: MALFORMED },
    ]),
  )(
    "$role with saved enabled $saved has no no-op action after OFF -> $state",
    async ({ role, config, degraded }) => {
      const { client } = openShell(role, config);
      await waitFor(() => expect(tabLabels()).toEqual(offTabs));
      await refresh(client, degraded);
      expect(client.getQueryState(key).status).toBe("success");
      expect(client.getQueryData(key).lastKnownDeploymentOff).toBe(true);
      expect(tabLabels()).toEqual(offTabs);
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
      expect(screen.queryByText("Owner or Admin only")).toBeNull();
      expect(screen.queryByText("MCP is off for this organization")).toBeNull();
      expect(screen.getByText("Not reported")).toBeInTheDocument();
      expect(
        screen.getByText(
          "The gateway did not report MCP as on for this organization",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Reload config" }),
      ).toBeEnabled();
      expect(
        screen.getByRole("button", { name: "Add stored settings" }),
      ).toBeEnabled();
      expect(writes()).toEqual([]);
      expect(
        api.adapter.mock.calls.every(([request]) =>
          [gw.config(id), gw.mcpStatus(id)].includes(request.url),
        ),
      ).toBe(true);
    },
  );

  it.each(adminCases)(
    "preserves cold-unknown base shell and working opt-in for $role, saved $saved",
    async ({ role, config }) => {
      status = UNKNOWN;
      const { client } = openShell(role, config);
      await screen.findByRole("button", { name: "Turn on MCP" });
      expect(tabLabels()).toEqual(baseTabs);
      expect(screen.getByRole("tab", { name: "Resources" })).toBeDisabled();
      expect(screen.getByRole("tab", { name: "Prompts" })).toBeDisabled();
      expect(client.getQueryData(key).lastKnownDeploymentOff).not.toBe(true);
      await expectWorkingOptIn();
    },
  );

  it.each(adminCases)(
    "restores working opt-in after an authoritative reply for $role, saved $saved",
    async ({ role, config }) => {
      const { client } = openShell(role, config);
      await waitFor(() => expect(tabLabels()).toEqual(offTabs));
      await refresh(client, UNKNOWN);
      // Do not use the button's presence to settle the retained-unknown render:
      // the pre-fix product already exposes it, but its handler is a no-op.
      await refresh(client, AVAILABLE);
      await waitFor(() => expect(tabLabels()).toEqual(baseTabs));
      expect(client.getQueryData(key).lastKnownDeploymentOff).not.toBe(true);
      await expectWorkingOptIn();
    },
  );

  it.each(["Member", "Reader"])(
    "keeps %s unable to opt in after a reliable runtime reply",
    async (role) => {
      const { client } = openShell(role, savedCases[0].config);
      await waitFor(() => expect(tabLabels()).toEqual(offTabs));
      await refresh(client, UNKNOWN);
      await refresh(client, AVAILABLE);
      await waitFor(() => expect(tabLabels()).toEqual(baseTabs));
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
      expect(writes()).toEqual([]);
    },
  );
});
