import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddMCPServerDialog from "./servers/AddMCPServerDialog";
import GuardrailsTab from "./guardrails/GuardrailsTab";
import OverviewTab from "./overview/OverviewTab";
import ServerCatalog from "./servers/ServerCatalog";
import ToolsGrid from "./servers/ToolsGrid";
import { serverRows } from "./servers/catalog";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. RelfupAdminGating.test.jsx covers the other levels.
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
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", gateway: null }),
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("./hooks/useMCPProbe", () => ({
  useMCPProbe: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
    reset: vi.fn(),
  }),
}));
vi.mock("./hooks/useMCPToolCalls", () => ({
  useMCPToolCalls: () => ({ data: undefined, isLoading: false }),
}));

describe("Gateway MCP status compatibility", () => {
  beforeEach(() => {
    mockRemoveMutate.mockReset();
    mockUpdateMutate.mockReset();
  });

  it("renders configured fallback status rows with id/status fields", () => {
    configQuery.current = {
      data: {
        mcp: { servers: { browser_smoke_mcp: { url: "http://x/mcp" } } },
      },
      isLoading: false,
    };
    render(<OverviewTab gatewayId="default" />);
    configQuery.current = { data: undefined, isLoading: false };

    expect(screen.getByText("Server health")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });

  it("matches server cards to id/status fallback health rows", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    const rows = serverRows(
      {
        servers: {
          browser_smoke_mcp: {
            url: "https://example.com/futureagi-mcp",
            transport: "http",
            tools_cache_ttl: "5m",
          },
        },
      },
      configuredStatus,
    );
    render(<ServerCatalog rows={rows} onOpen={onOpen} />);

    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("0 tools")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /browser_smoke_mcp/ }));
    expect(onOpen).toHaveBeenCalledWith("browser_smoke_mcp");
  });

  it("shows a stored allowed server by its id (read-only)", () => {
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

    expect(screen.getByText("Allowed servers")).toBeInTheDocument();
    expect(screen.getByText("browser_smoke_mcp")).toBeInTheDocument();
  });

  it("shows an explicit all-servers label for the tool filter, and an empty state", () => {
    const { unmount } = render(
      <ToolsGrid
        tools={[{ name: "a__b", server_id: "a", upstream_name: "b" }]}
        isLoading={false}
      />,
    );
    expect(screen.getByText("All servers")).toBeInTheDocument();
    unmount();

    render(<ToolsGrid tools={[]} isLoading={false} />);
    expect(screen.getByText("No tools yet")).toBeInTheDocument();
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
