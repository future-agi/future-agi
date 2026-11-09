// Every MCP caller of a config write, on a save Django stored but the
// gateway did not apply (gateway_synced:false). The hooks make the callers'
// success toast unreachable and keep their generic "Failed to ..." toast
// quiet. Each caller shows exactly one toast, the warning, and a dialog keeps
// its Alert with the same sentence, as a warning.
//
// Callers (grep useUpdateMCPServer|useRemoveMCPServer|useUpdateMCPGuardrails|
// useReloadConfig under sections/gateway/mcp):
//   servers/AddMCPServerDialog.jsx   useUpdateMCPServer (add and edit)
//   servers/ServerDrawer.jsx         useRemoveMCPServer
//   MCPManagementSection.jsx         useReloadConfig (Reload config)
//   guardrails/GuardrailsTab.jsx     useUpdateMCPGuardrails (Turn off)
// The MCP Guardrails tab shows the stored section read-only (the gateway
// does not apply it); its one write turns stored guardrails off.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";

import AddMCPServerDialog from "../servers/AddMCPServerDialog";
import ServerDrawer from "../servers/ServerDrawer";
import { serverRows } from "../servers/catalog";
import MCPManagementSection from "../MCPManagementSection";
import GuardrailsTab from "../guardrails/GuardrailsTab";

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

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. RelfupAdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        reload: p("reload"),
        updateMcpServer: p("update-mcp-server"),
        removeMcpServer: p("remove-mcp-server"),
        updateMcpGuardrails: p("update-mcp-guardrails"),
        mcpStatus: p("mcp-status"),
        mcpTools: p("mcp-tools"),
        mcpResources: p("mcp-resources"),
        mcpPrompts: p("mcp-prompts"),
      },
    },
  };
});

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));

// The section's tab bodies are not under test here: only its Reload button.
vi.mock("../overview/OverviewTab", () => ({ default: () => null }));
vi.mock("../servers/ServersTab", () => ({ default: () => null }));
vi.mock("../playground/PlaygroundTab", () => ({ default: () => null }));
vi.mock("../resources/ResourcesTab", () => ({ default: () => null }));
vi.mock("../prompts/PromptsTab", () => ({ default: () => null }));

const STORED = {
  mcp: {
    servers: { github: { transport: "http", url: "http://mcp:8080" } },
    guardrails: { enabled: false },
  },
};

const unsynced = {
  data: {
    status: true,
    result: { gateway_synced: false, gateway_warning: WARNING },
  },
};

let client;
function renderWithClient(ui) {
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  // test-utils' render adds the router and the theme.
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </HelmetProvider>,
  );
}

function expectOnlyTheWarning() {
  expect(snackbars).toEqual([
    { message: SENTENCE, options: { variant: "warning" } },
  ]);
}

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  get.mockImplementation(async (url) => {
    if (url.endsWith("/config/"))
      return { data: { status: true, result: STORED } };
    if (url.endsWith("/mcp-status/"))
      return { data: { status: true, result: { enabled: true, servers: [] } } };
    return { data: { status: true, result: [] } };
  });
  post.mockResolvedValue(unsynced);
});

describe("NG-34 MCP callers on gateway_synced:false", () => {
  it("Add MCP Server: the warning, no 'added' toast, the dialog stays open showing it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <AddMCPServerDialog open onClose={onClose} gatewayId="gw-1" />,
    );
    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080");
    await user.click(screen.getByRole("button", { name: "Add server" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-mcp-server/",
    );
    const alert = await screen.findByText(SENTENCE);
    expect(alert.closest(".MuiAlert-root").className).toMatch(/Warning/);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Delete server: the warning, no 'removed' and no 'Failed to remove' toast", async () => {
    const user = userEvent.setup();
    const [row] = serverRows(STORED.mcp, { servers: [] }, []);
    renderWithClient(
      <ServerDrawer
        row={row}
        tools={[]}
        gatewayId="gw-1"
        onClose={() => {}}
        onEdit={() => {}}
      />,
    );
    const drawer = await screen.findByRole("dialog", { name: "github" });
    await user.click(within(drawer).getByRole("button", { name: "Remove" }));
    await user.click(
      await screen.findByRole("button", { name: "Remove server" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/remove-mcp-server/",
    );
  });

  it("Reload Config on the MCP page: the warning, no 'Configuration reloaded' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<MCPManagementSection />);
    await user.click(
      await screen.findByRole("button", { name: "Reload config" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/reload/");
  });

  it("Turn off on the MCP Guardrails tab: the warning, no 'turned off' toast, every other stored value sent back", async () => {
    const stored = {
      enabled: true,
      blocked_tools: ["shell"],
      allowed_servers: ["github"],
    };
    get.mockImplementation(async (url) => {
      if (url.endsWith("/config/"))
        return {
          data: {
            status: true,
            result: { mcp: { ...STORED.mcp, guardrails: stored } },
          },
        };
      return { data: { status: true, result: [] } };
    });
    const user = userEvent.setup();
    renderWithClient(<GuardrailsTab gatewayId="gw-1" />);
    await user.click(await screen.findByRole("button", { name: "Turn off" }));
    await user.click(screen.getByRole("button", { name: "Turn off" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-mcp-guardrails/",
    );
    expect(JSON.stringify(post.mock.calls[0][1])).toBe(
      JSON.stringify({ config: { ...stored, enabled: false } }),
    );
  });

  it("control: a synced add still says 'added'", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: { gateway_synced: true, server: "github" },
      },
    });
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <AddMCPServerDialog open onClose={onClose} gatewayId="gw-1" />,
    );
    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080");
    await user.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(snackbars).toEqual([
      { message: 'Server "github" added', options: { variant: "success" } },
    ]);
  });
});
