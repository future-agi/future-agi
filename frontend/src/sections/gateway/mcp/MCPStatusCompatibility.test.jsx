import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddMCPServerDialog from "./servers/AddMCPServerDialog";
import GuardrailsTab from "./guardrails/GuardrailsTab";
import OverviewTab from "./overview/OverviewTab";
import ServerCards from "./servers/ServerCards";
import ToolsTable from "./servers/ToolsTable";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { mockRemoveMutate, mockUpdateMutate, configuredStatus, configQuery } =
  vi.hoisted(() => ({
    mockRemoveMutate: vi.fn(),
    mockUpdateMutate: vi.fn(),
    configuredStatus: {
      enabled: true,
      sessions: 0,
      tools: 0,
      resources: 0,
      prompts: 0,
      servers: [{ id: "browser_smoke_mcp", status: "configured" }],
    },
    configQuery: { current: { data: undefined, isLoading: false } },
  }));

vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: configuredStatus, isLoading: false }),
  useRemoveMCPServer: () => ({ mutate: mockRemoveMutate, isPending: false }),
  useUpdateMCPServer: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useUpdateMCPGuardrails: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
  }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => configQuery.current,
}));

describe("Gateway MCP status compatibility", () => {
  beforeEach(() => {
    mockRemoveMutate.mockReset();
    mockUpdateMutate.mockReset();
  });

  it("renders configured fallback status rows with id/status fields", () => {
    render(<OverviewTab gatewayId="default" />);

    expect(screen.getByText("Server Health")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });

  it("matches server cards to id/status fallback health rows", async () => {
    const onEditServer = vi.fn();
    const user = userEvent.setup();

    render(
      <ServerCards
        gatewayId="default"
        config={{
          mcp: {
            servers: {
              browser_smoke_mcp: {
                url: "https://example.com/futureagi-mcp",
                transport: "http",
                tools_cache_ttl: "5m",
              },
            },
          },
        }}
        mcpStatus={configuredStatus}
        onEditServer={onEditServer}
      />,
    );

    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("0 tools")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /edit/i }));
    expect(onEditServer).toHaveBeenCalledWith(
      "browser_smoke_mcp",
      expect.objectContaining({ transport: "http" }),
    );
  });

  it("uses id/status fallback server ids as guardrail allowed-server options", () => {
    configQuery.current = {
      data: {
        mcp: {
          guardrails: {
            enabled: true,
            allowed_servers: ["browser_smoke_mcp"],
          },
        },
      },
      isLoading: false,
    };
    render(<GuardrailsTab gatewayId="default" />);

    expect(screen.getByText("Allowed Servers")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
  });

  it("shows an explicit all-servers label for the empty tool filter", () => {
    render(<ToolsTable mcpTools={[]} isLoading={false} />);

    expect(screen.getByText("All Servers")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No MCP tools registered. Connect an MCP server to discover tools.",
      ),
    ).toBeInTheDocument();
  });

  it("prefills edit dialogs created from serverId props", () => {
    render(
      <AddMCPServerDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        editServer={{
          serverId: "browser_smoke_mcp",
          config: {
            url: "https://example.com/futureagi-mcp",
            transport: "http",
          },
        }}
      />,
    );

    expect(screen.getByDisplayValue("browser_smoke_mcp")).toBeDisabled();
  });
});
