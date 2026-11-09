import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { endpoints } from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import { DEMO_MCP, renderMcp } from "./testing/renderMcp";
import { MCP_API } from "./api";
import { consentListPath } from "./clients/consentApi";

// Reuse the 1009 transport/config and grid-fixture pattern, not tab stand-ins.
// The real parent, saved controls, status/retention hooks and numeric role
// predicate run here. Organization/auth contexts supply identity only.
const api = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(),
  request: vi.fn(), put: vi.fn(), head: vi.fn(), options: vi.fn(),
  orgLevel: 15, canManage: true,
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: {
    get: api.get, post: api.post, patch: api.patch, delete: api.delete,
    request: api.request, put: api.put, head: api.head, options: api.options,
  },
}));
vi.mock("notistack", async (original) => ({
  ...(await original()), enqueueSnackbar: vi.fn(),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: api.orgLevel, currentOrganizationId: "org-siem-test" }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { id: "siem-test-user" } }) }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1009", gateway: {} }),
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));

const gw = endpoints.gateway;
const methods = ["get", "post", "patch", "delete", "request", "put", "head", "options"];
const roles = [["Owner", 15, true], ["Admin", 8, true], ["Member", 3, false], ["Reader", 1, false]];
// Explicit MCP_OFF_TABS contract: deriving expectations dynamically from the
// implementation would let removing a tab also remove its assertion.
const offTabs = ["Overview", "Tools", "Servers", "Resources", "Prompts", "Guardrails", "Playground"];
let status;
let config;
let unexpected;
const clients = [];
const result = (value) => ({ data: { status: true, result: value } });
function onStatus(clientOAuth) {
  return {
    enabled: true, servers: [],
    features: { siem_export: true, tool_call_explorer: false, tool_call_download: false, client_oauth: clientOAuth },
  };
}
function noSiemRequests() {
  for (const method of methods) {
    expect(api[method].mock.calls.filter(([target]) => String(target?.url || target).includes("/siem/"))).toEqual([]);
  }
  expect(unexpected).toEqual([]);
}
function clearCalls() { methods.forEach((method) => api[method].mockClear()); }
async function refreshStatus(view, next) {
  status = next;
  await act(async () => {
    await view.client.invalidateQueries({ queryKey: ["agentcc-mcp-status", "gw-1009"] });
  });
  // Wait for the actual observer render, not only the refetch promise/cache.
  if (next instanceof Error) await screen.findByText("MCP status could not be loaded");
  else if (next.reason) await screen.findByText(next.reason, { selector: "code" });
  else if (next.enabled === true) await screen.findByRole("button", { name: "SIEM export" });
  else if (next.enabled === false) await screen.findByText("MCP is off for this organization");
  else await screen.findByText("The gateway did not report MCP as on for this organization");
}
async function selectTab(label) {
  const tab = screen.getByRole("tab", { name: label, exact: true });
  expect(tab).toBeEnabled();
  fireEvent.click(tab);
  await waitFor(() => expect(tab).toHaveAttribute("aria-selected", "true"));
}
async function closeDialog(dialog) {
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
}
async function expectSevenRealContents(clientOAuth, canEdit) {
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    ...offTabs, ...(clientOAuth ? ["Manage MCP clients"] : []),
  ]);
  for (const label of offTabs) {
    await selectTab(label);
    if (label === "Overview") {
      expect(await screen.findByRole("heading", { name: "Saved servers" })).toBeInTheDocument();
      expect(screen.getByText("alpha", { exact: true })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "All servers" })).toBeEnabled();
    } else if (["Tools", "Resources", "Prompts"].includes(label)) {
      const search = screen.getByPlaceholderText(`Search ${label.toLowerCase()}`);
      expect(search).toBeEnabled();
      fireEvent.change(search, { target: { value: "no-match" } });
      expect(search).toHaveValue("no-match");
      expect(screen.getByText(/Live MCP operations are unavailable/)).toBeInTheDocument();
    } else if (label === "Servers") {
      expect(await screen.findByText("alpha", { exact: true })).toBeInTheDocument();
      expect(screen.getByPlaceholderText("Search servers")).toBeEnabled();
      expect(screen.getByRole("button", { name: "Add server" })).toBeInTheDocument();
    } else if (label === "Guardrails") {
      expect(screen.getByTestId("mcp-guardrails-recorded-only")).toHaveTextContent("Organization MCP guardrails");
      expect(screen.getByRole("button", { name: "Edit stored settings" })).toBeInTheDocument();
    } else {
      const args = screen.getByLabelText("Arguments (JSON)");
      expect(args).toBeEnabled();
      fireEvent.change(args, { target: { value: '{"query":"local"}' } });
      expect(args).toHaveValue('{"query":"local"}');
      const run = screen.getByRole("button", { name: "Run" });
      expect(run).toBeDisabled();
      fireEvent.click(run);
      expect(api.post.mock.calls.filter(([url]) => url === gw.testMcpTool("gw-1009"))).toEqual([]);
    }
    noSiemRequests();
  }
  if (clientOAuth) {
    await selectTab("Manage MCP clients");
    expect(await screen.findByText(/You have no active MCP client connections/)).toBeInTheDocument();
    const organization = screen.queryByRole("button", { name: "Organization clients" });
    if (canEdit) expect(organization).toBeEnabled();
    else expect(organization).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("button", { name: "Turn on MCP" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "SIEM export" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // The OFF matrix cannot be satisfied by live-list requests returning empty.
  expect(api.get.mock.calls.filter(([url]) => ![
    gw.config("gw-1009"), gw.mcpStatus("gw-1009"), consentListPath(),
  ].includes(url))).toEqual([]);
  noSiemRequests();
}
async function expectSavedRoleControls(canEdit, runtimeOff) {
  const reload = screen.getByRole("button", { name: "Reload config" });
  if (canEdit) expect(reload).toBeEnabled();
  else expect(reload).toBeDisabled();
  api.post.mockClear();
  fireEvent.click(reload);
  if (canEdit) {
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(gw.reload("gw-1009"), {}, { timeout: 30000 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Reload config" })).toBeEnabled());
  } else expect(api.post).not.toHaveBeenCalled();

  await selectTab("Servers");
  const add = await screen.findByRole("button", { name: "Add server" });
  if (canEdit) expect(add).toBeEnabled();
  else expect(add).toBeDisabled();
  fireEvent.click(add);
  if (canEdit) {
    const dialog = await screen.findByRole("dialog");
    const serverId = within(dialog).getByLabelText(/Server ID/);
    expect(serverId).toBeEnabled();
    fireEvent.change(serverId, { target: { value: "beta" } });
    fireEvent.change(within(dialog).getByLabelText(/URL/), { target: { value: config.mcp.servers.alpha.url } });
    if (runtimeOff) expect(within(dialog).getByRole("button", { name: "Test connection" })).toBeDisabled();
    await closeDialog(dialog);
  } else expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await selectTab("Guardrails");
  const edit = await screen.findByRole("button", { name: "Edit stored settings" });
  if (canEdit) expect(edit).toBeEnabled();
  else expect(edit).toBeDisabled();
  api.post.mockClear();
  fireEvent.click(edit);
  if (canEdit) {
    const dialog = await screen.findByRole("dialog");
    const validate = within(dialog).getByLabelText("Validate tool outputs");
    expect(validate).toBeEnabled();
    fireEvent.click(validate);
    const expected = { ...config.mcp.guardrails, validate_outputs: !config.mcp.guardrails.validate_outputs };
    fireEvent.click(within(dialog).getByRole("button", { name: "Save stored settings" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(gw.updateMcpGuardrails("gw-1009"), { config: expected }));
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
  } else {
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  }
  noSiemRequests();
}
async function openSiem(view, canEdit) {
  fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
  expect(await screen.findByText("No destinations yet.")).toBeInTheDocument();
  const add = screen.queryByText("Add a destination", { exact: true });
  if (canEdit) expect(add).toBeInTheDocument();
  else {
    expect(add).not.toBeInTheDocument();
    expect(screen.getByText(/Read-only\. Only an organization Owner or Admin/)).toBeInTheDocument();
  }
  await waitFor(() => expect(api.get).toHaveBeenCalledWith(MCP_API.siemDeliveries, { params: { status: "dead_letter" } }));
  await waitFor(() => expect(view.client.isFetching({ queryKey: ["agentcc-siem"] })).toBe(0));
}

beforeEach(() => {
  status = onStatus(false);
  config = {
    version: 1,
    mcp: {
      enabled: false, servers: { alpha: { ...DEMO_MCP.servers["crm-tools"] } },
      guardrails: { enabled: true, validate_inputs: true, validate_outputs: false, blocked_tools: [], allowed_servers: ["alpha"], tool_rate_limits: {}, extension: "preserved" },
    },
  };
  unexpected = [];
  for (const method of methods) {
    api[method].mockReset().mockImplementation(async () => {
      unexpected.push(method);
      throw new Error("Unexpected transport method");
    });
  }
  api.get.mockImplementation(async (url) => {
    if (url === gw.config("gw-1009")) return result(config);
    if (url === gw.mcpStatus("gw-1009")) {
      if (status instanceof Error) throw status;
      return result(status);
    }
    // ON list fixtures for the always-mounted ServerDrawer, even without a row.
    // The strict OFF assertions still forbid every live-list request.
    if ([
      gw.mcpTools("gw-1009"), gw.mcpResources("gw-1009"), gw.mcpPrompts("gw-1009"), consentListPath(),
    ].includes(url)) return result([]);
    if (url === MCP_API.siemDestinations) return result({ export_available: true, can_manage: api.canManage, destinations: [] });
    if (url === MCP_API.siemDeliveries) return result({ deliveries: [] });
    unexpected.push("get");
    throw new Error("Unexpected GET");
  });
  api.post.mockImplementation(async (url) => {
    if (![gw.reload("gw-1009"), gw.updateMcpGuardrails("gw-1009")].includes(url)) {
      unexpected.push("post");
      throw new Error("Unexpected POST");
    }
    return result({ gateway_synced: true });
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("SIEM closure preserves actual 1009 saved controls", () => {
  it.each(roles.flatMap((role) => [false, true].map((clientOAuth) => [...role, clientOAuth])))(
    "%s level=%s canEdit=%s clients=%s through OFF, degraded and recovery",
    async (_role, level, canEdit, clientOAuth) => {
      api.orgLevel = level;
      api.canManage = canEdit;
      status = onStatus(clientOAuth);
      const view = renderMcp(<MCPManagementSection />, { route: "/dashboard/gateway/mcp?tab=servers" });
      clients.push(view.client);
      expect(await screen.findByText("alpha", { exact: true })).toBeInTheDocument();
      await expectSavedRoleControls(canEdit, false);
      await openSiem(view, canEdit);
      // Finish all ON queries before starting the strict OFF request ledger.
      await waitFor(() => expect(view.client.isFetching()).toBe(0));
      clearCalls();
      const features = status.features;
      await refreshStatus(view, { enabled: false, reason: "gateway_mcp_disabled", features });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await expectSevenRealContents(clientOAuth, canEdit);
      await expectSavedRoleControls(canEdit, true);
      for (const next of [
        { enabled: true, reason: "unknown", features },
        { enabled: true, reason: "gateway_unreachable", features },
        { features },
        new Error("Status transport failed"),
      ]) {
        clearCalls();
        await refreshStatus(view, next);
        await expectSevenRealContents(!(next instanceof Error) && clientOAuth, canEdit);
        await expectSavedRoleControls(canEdit, true);
      }
      clearCalls();
      await refreshStatus(view, onStatus(clientOAuth));
      await waitFor(() => expect(screen.getByRole("button", { name: "SIEM export" })).toBeEnabled());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      noSiemRequests();
      await expectSavedRoleControls(canEdit, false);
      await waitFor(() => expect(view.client.isFetching()).toBe(0));
      // Saved org-off opt-in remains role-restricted after deployment recovery.
      await refreshStatus(view, { enabled: false, features });
      const turnOn = screen.queryByRole("button", { name: "Turn on MCP" });
      if (canEdit) expect(turnOn).toBeEnabled();
      else expect(turnOn).not.toBeInTheDocument();
      noSiemRequests();
      await refreshStatus(view, onStatus(clientOAuth));
      await waitFor(() => expect(screen.getByRole("button", { name: "SIEM export" })).toBeEnabled());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      noSiemRequests();
      await openSiem(view, canEdit);
    },
  );
});
