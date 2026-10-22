// Every MCP caller of a config write, on a save Django
// stored but the gateway did not apply (gateway_synced:false). The hooks
// make the caller's success toast unreachable and keep its generic
// "Failed to ..." toast quiet. Each caller shows exactly one
// toast, the warning, and a dialog keeps its Alert with the same sentence.
//
// Callers (grep useUpdateMCPServer|useRemoveMCPServer|useUpdateMCPGuardrails|
// useReloadConfig under sections/gateway/mcp):
//   servers/AddMCPServerDialog.jsx   useUpdateMCPServer (add and edit)
//   servers/ServerCards.jsx          useRemoveMCPServer
//   guardrails/GuardrailsTab.jsx     useUpdateMCPGuardrails
//   MCPManagementSection.jsx         useReloadConfig (Reload Config)
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddMCPServerDialog from "../servers/AddMCPServerDialog";
import ServerCards from "../servers/ServerCards";
import GuardrailsTab from "../guardrails/GuardrailsTab";
import MCPManagementSection from "../MCPManagementSection";

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
// act as an Admin. AdminGating.test.jsx covers the other levels.
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
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
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

describe("MCP callers on gateway_synced:false", () => {
  it("Add MCP Server: the warning, no 'added' toast, the dialog stays open showing it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <AddMCPServerDialog open onClose={onClose} gatewayId="gw-1" />,
    );
    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080");
    await user.click(screen.getByRole("button", { name: "Add Server" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-mcp-server/",
    );
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Delete server: the warning, no 'removed' and no 'Failed to remove' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <ServerCards
        config={STORED}
        mcpStatus={{ servers: [] }}
        gatewayId="gw-1"
        onEditServer={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Remove" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/remove-mcp-server/",
    );
  });

  it("MCP Guardrails save: the warning and the tab's Alert, no 'updated' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<GuardrailsTab gatewayId="gw-1" />);
    await user.click(
      await screen.findByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    await user.click(await screen.findByRole("button", { name: /Save/ }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-mcp-guardrails/",
    );
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
  });

  it("MCP Guardrails save: a stored save leaves no 'unsaved changes' note, and its Alert is a warning", async () => {
    const user = userEvent.setup();
    renderWithClient(<GuardrailsTab gatewayId="gw-1" />);
    await user.click(
      await screen.findByRole("checkbox", { name: "Enable MCP Guardrails" }),
    );
    expect(screen.getByText("You have unsaved changes.")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /Save/ }));

    const alert = await screen.findByText(SENTENCE);
    await waitFor(() =>
      expect(screen.queryByText("You have unsaved changes.")).toBeNull(),
    );
    expect(alert.closest(".MuiAlert-root").className).toMatch(/Warning/);
  });

  it("Reload Config on the MCP page: the warning, no 'Configuration reloaded' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<MCPManagementSection />);
    await user.click(
      await screen.findByRole("button", { name: "Reload Config" }),
    );

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/reload/");
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
    await user.click(screen.getByRole("button", { name: "Add Server" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(snackbars).toEqual([
      { message: 'Server "github" added', options: { variant: "success" } },
    ]);
  });
});
