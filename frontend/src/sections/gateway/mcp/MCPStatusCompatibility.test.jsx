import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddMCPServerDialog from "./AddMCPServerDialog";
import MCPGuardrailsTab from "./MCPGuardrailsTab";
import MCPOverviewTab from "./MCPOverviewTab";
import MCPServersTab from "./MCPServersTab";
import MCPToolsTab from "./MCPToolsTab";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The role check: editing and removing a server are
// Owner or Admin only. The cases below run as an Admin unless one says
// otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const ONE_SERVER = {
  mcp: {
    servers: {
      browser_smoke_mcp: {
        url: "https://example.com/futureagi-mcp",
        transport: "http",
      },
    },
  },
};

const { mockRemoveMutate, mockUpdateMutate } = vi.hoisted(() => ({
  mockRemoveMutate: vi.fn(),
  mockUpdateMutate: vi.fn(),
}));

vi.mock("./hooks/useMCPConfig", () => ({
  useRemoveMCPServer: () => ({ mutate: mockRemoveMutate, isPending: false }),
  useUpdateMCPServer: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useUpdateMCPGuardrails: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
  }),
}));

const configuredStatus = {
  enabled: true,
  sessions: 0,
  tools: 0,
  resources: 0,
  prompts: 0,
  servers: [{ id: "browser_smoke_mcp", status: "configured" }],
};

describe("Gateway MCP status compatibility", () => {
  beforeEach(() => {
    mockOrgLevel = 8;
    mockRemoveMutate.mockReset();
    mockUpdateMutate.mockReset();
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads a server card with Edit and Delete disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(
        <MCPServersTab
          gatewayId="default"
          config={ONE_SERVER}
          mcpStatus={configuredStatus}
          onEditServer={vi.fn()}
        />,
      );
      expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: /edit/i }));
      expectAdminOnly(screen.getByRole("button", { name: /delete/i }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can edit and delete a server", (_who, level) => {
    mockOrgLevel = level;
    render(
      <MCPServersTab
        gatewayId="default"
        config={ONE_SERVER}
        mcpStatus={configuredStatus}
        onEditServer={vi.fn()}
      />,
    );
    expectEditable(screen.getByRole("button", { name: /edit/i }));
    expectEditable(screen.getByRole("button", { name: /delete/i }));
  });

  it("renders configured fallback status rows with id/status fields", () => {
    render(<MCPOverviewTab mcpStatus={configuredStatus} />);

    expect(screen.getByText("Server Health")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });

  it("matches server cards to id/status fallback health rows", async () => {
    const onEditServer = vi.fn();
    const user = userEvent.setup();

    render(
      <MCPServersTab
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
    render(
      <MCPGuardrailsTab
        gatewayId="default"
        config={{
          mcp: {
            guardrails: {
              enabled: true,
              allowed_servers: ["browser_smoke_mcp"],
            },
          },
        }}
        mcpStatus={configuredStatus}
      />,
    );

    expect(screen.getByText("Allowed Servers")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
  });

  it("shows an explicit all-servers label for the empty tool filter", () => {
    render(<MCPToolsTab mcpTools={[]} isLoading={false} />);

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
