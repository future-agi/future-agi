import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { endpoints } from "src/utils/axios";
import MCPManagementSection from "./MCPManagementSection";
import { renderMcp } from "./testing/renderMcp";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), canEdit: true }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: api.get, post: api.post },
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: api.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1009", gateway: {} }),
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("./testing/agGridMock")).MockAgGrid,
}));

const gw = endpoints.gateway;
const OFF = {
  enabled: false,
  reason: "gateway_mcp_disabled",
  servers: [],
  features: {
    client_oauth: true,
    client_installs: true,
    tool_call_explorer: true,
    tool_call_downloads: true,
    credential_vault: true,
    virtual_servers: true,
  },
};
const BASE_TABS = [
  "Overview",
  "Tools",
  "Servers",
  "Resources",
  "Prompts",
  "Guardrails",
  "Playground",
  "Manage MCP clients",
];
const NEW = [
  ["credentials", "Credentials"],
  ["virtual", "Virtual servers"],
  ["connect", "Connect"],
  ["calls", "Tool calls"],
  ["registry", "Registry"],
  ["trust", "Tool revisions"],
  ["approvals", "Approvals"],
  ["downloads", "Downloads"],
  ["toolkits", "Toolkits"],
];
let config;
let status;
const clients = [];
function open(slug = "overview", extra = "") {
  const view = renderMcp(<MCPManagementSection />, {
    route: `/dashboard/gateway/mcp?tab=${slug}${extra}`,
  });
  clients.push(view.client);
  return view;
}
async function ready() {
  expect(
    await screen.findByRole("tab", { name: "Overview" }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Resources" })).toBeEnabled(),
  );
}
function onlySavedReads() {
  expect(
    api.get.mock.calls
      .map(([url]) => url)
      .filter(
        (url) => ![gw.config("gw-1009"), gw.mcpStatus("gw-1009")].includes(url),
      ),
  ).toEqual([]);
}
beforeEach(() => {
  api.canEdit = true;
  status = OFF;
  config = {
    version: 1,
    mcp: {
      enabled: false,
      servers: {
        alpha: { url: "https://alpha.example/mcp", transport: "http" },
      },
      guardrails: {
        enabled: true,
        validate_inputs: true,
        validate_outputs: false,
        blocked_tools: ["alpha__delete"],
        allowed_servers: ["alpha"],
        tool_rate_limits: { alpha__read: 5 },
        extension: "preserved",
      },
    },
  };
  api.get.mockReset().mockImplementation(async (url) => {
    if (url === gw.config("gw-1009")) return { data: { result: config } };
    if (url === gw.mcpStatus("gw-1009")) return { data: { result: status } };
    throw new Error(`Unexpected request: ${String(url)}`);
  });
  api.post
    .mockReset()
    .mockResolvedValue({ data: { result: { gateway_synced: true } } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("RL-1009 explicit MCP-off allowlist", () => {
  it("restores exactly the baseline tabs plus existing client management beneath the notice", async () => {
    open();
    await ready();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
      BASE_TABS,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      /MCP is turned off on this gateway/,
    );
    expect(screen.getByRole("button", { name: "Reload config" })).toBeEnabled();
    expect(
      screen.queryByText(/healthy|Recent tool calls|Virtual servers/i),
    ).toBeNull();
    onlySavedReads();
    expect(api.post).not.toHaveBeenCalled();
    expect(
      screen.queryByText("Manage MCP servers and saved settings."),
    ).toBeNull();
  });
  it.each(NEW)(
    "does not render or request new %s even when independent capabilities are true",
    async (slug, label) => {
      open(slug);
      await ready();
      expect(
        screen.queryByRole("tab", { name: label, exact: true }),
      ).toBeNull();
      expect(
        screen.queryByRole("button", {
          name: /Download|Export CSV|Export JSON|Manage credentials/,
        }),
      ).toBeNull();
      onlySavedReads();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
  it.each(["tools", "resources", "prompts"])(
    "keeps the %s local filter usable without live reads",
    async (slug) => {
      open(slug);
      await ready();
      const search = await screen.findByPlaceholderText(`Search ${slug}`);
      expect(search).toBeEnabled();
      fireEvent.change(search, { target: { value: "does-not-exist" } });
      expect(search).toHaveValue("does-not-exist");
      expect(
        screen.getByText(/Live MCP operations are unavailable/),
      ).toBeInTheDocument();
      onlySavedReads();
    },
  );
  it("keeps local arguments editable while Run is disabled even with a tool deep link", async () => {
    open("playground", "&tool=alpha__read");
    await ready();
    const args = screen.getByLabelText("Arguments (JSON)");
    fireEvent.change(args, { target: { value: '{"query":"local"}' } });
    expect(args).toHaveValue('{"query":"local"}');
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    onlySavedReads();
    expect(api.post).not.toHaveBeenCalled();
  });
  it.each(["Owner", "Admin", "Member", "Reader"])(
    "retains saved-control permissions for %s",
    async (role) => {
      api.canEdit = ["Owner", "Admin"].includes(role);
      open("servers");
      await ready();
      expect(await screen.findByText("alpha")).toBeInTheDocument();
      const reload = screen.getByRole("button", { name: "Reload config" });
      const add = screen.getByRole("button", { name: "Add server" });
      if (!api.canEdit) {
        expect(reload).toBeDisabled();
        expect(add).toBeDisabled();
        fireEvent.click(add);
        fireEvent.click(reload);
        expect(api.post).not.toHaveBeenCalled();
        return;
      }
      expect(reload).toBeEnabled();
      expect(add).toBeEnabled();
      fireEvent.click(reload);
      await waitFor(() => expect(api.post).toHaveBeenCalled());
      fireEvent.click(add);
      const dialog = await screen.findByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText(/Server ID/), {
        target: { value: "beta" },
      });
      fireEvent.change(within(dialog).getByLabelText(/URL/), {
        target: { value: "https://beta.example/mcp" },
      });
      expect(
        within(dialog).getByRole("button", { name: "Test connection" }),
      ).toBeDisabled();
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Add server" }),
      );
      await waitFor(() =>
        expect(api.post).toHaveBeenCalledWith(gw.updateMcpServer("gw-1009"), {
          server_id: "beta",
          config: { transport: "http", url: "https://beta.example/mcp" },
        }),
      );
      expect(config.mcp.enabled).toBe(false);
    },
  );
  it.each(["Owner", "Admin", "Member", "Reader"])(
    "keeps 850 stored editing and its role boundary for %s",
    async (role) => {
      api.canEdit = ["Owner", "Admin"].includes(role);
      open("guardrails");
      await ready();
      const edit = await screen.findByRole("button", {
        name: "Edit stored settings",
      });
      if (!api.canEdit) {
        expect(edit).toBeDisabled();
        fireEvent.click(edit);
        expect(api.post).not.toHaveBeenCalled();
        return;
      }
      fireEvent.click(edit);
      const dialog = await screen.findByRole("dialog");
      for (const label of [
        "Enabled in stored settings",
        "Validate tool inputs",
        "Validate tool outputs",
        "Blocked tools",
        "Allowed servers",
        "Stored per-tool limits",
      ]) {
        expect(within(dialog).getByLabelText(label)).toBeEnabled();
      }
      fireEvent.click(within(dialog).getByLabelText("Validate tool outputs"));
      fireEvent.change(within(dialog).getByLabelText("Blocked tools"), {
        target: { value: "alpha__write" },
      });
      fireEvent.change(within(dialog).getByLabelText("Allowed servers"), {
        target: { value: "alpha\nbeta" },
      });
      fireEvent.change(
        within(dialog).getByLabelText("Stored per-tool limits"),
        { target: { value: '{"alpha__read":10}' } },
      );
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Save stored settings" }),
      );
      await waitFor(() =>
        expect(api.post).toHaveBeenCalledWith(
          gw.updateMcpGuardrails("gw-1009"),
          {
            config: {
              ...config.mcp.guardrails,
              validate_outputs: true,
              blocked_tools: ["alpha__write"],
              allowed_servers: ["alpha", "beta"],
              tool_rate_limits: { alpha__read: 10 },
            },
          },
        ),
      );
    },
  );
  it.each(["Owner", "Admin", "Member", "Reader"])(
    "preserves the org opt-in permission for %s when deployment is on",
    async (role) => {
      api.canEdit = ["Owner", "Admin"].includes(role);
      status = { enabled: false, servers: [] };
      open("guardrails");
      expect(
        await screen.findByText("MCP is off for this organization"),
      ).toBeInTheDocument();
      const turnOn = screen.queryByRole("button", { name: "Turn on MCP" });
      if (api.canEdit) expect(turnOn).toBeEnabled();
      else expect(turnOn).toBeNull();
      expect(screen.getByRole("tab", { name: "Resources" })).toBeDisabled();
      expect(screen.getByRole("tab", { name: "Prompts" })).toBeDisabled();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
});
