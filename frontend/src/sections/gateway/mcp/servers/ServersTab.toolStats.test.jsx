import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ServersTab from "./ServersTab";

// The Servers tab's Tools list carries each tool's call stats (toolStats.js).

const grid = vi.hoisted(() => ({ columnDefs: [] }));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  // eslint-disable-next-line react/prop-types
  function RecordingGrid({ columnDefs, ...props }) {
    grid.columnDefs = columnDefs;
    return <MockAgGrid columnDefs={columnDefs} {...props} />;
  }
  return { AgGridReact: RecordingGrid };
});

vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: {
      mcp: {
        enabled: true,
        servers: {
          github: {
            url: "https://mcp.example.com/github/mcp",
            transport: "http",
          },
        },
      },
    },
    isLoading: false,
  }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: {
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
      },
      enabled: true,
      servers: [{ server_id: "github", healthy: true, tool_count: 1 }],
    },
  }),
  useMCPTools: () => ({
    data: [
      {
        name: "github__create_issue",
        server_id: "github",
        upstream_name: "create_issue",
        quarantined: false,
        stats: {
          call_count: 12,
          error_count: 1,
          avg_latency_ms: 240.5,
          error_rate: 0.0833,
          last_called: null,
          window_days: 30,
        },
      },
    ],
    isLoading: false,
  }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMCPServer: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useMCPCredentials", () => ({
  useMCPCredentials: () => ({ data: { rows: [], count: 0 } }),
}));
vi.mock("../hooks/useMCPToolCalls", () => ({
  useMCPToolCalls: () => ({ data: { pages: [] }, isLoading: false }),
}));

describe("ServersTab: the Tools list's call stats", () => {
  it("shows each tool's calls of the last 30 days", () => {
    renderMcp(<ServersTab gatewayId="gw-1" />);
    expect(grid.columnDefs.map((c) => c.headerName)).toContain(
      "Calls (30 days)",
    );
    const row = screen
      .getAllByRole("row")
      .find((r) => r.textContent.includes("github__create_issue"));
    const text = (colId) =>
      row.querySelector(`[data-col="${colId}"]`).textContent;
    expect(text("calls")).toBe("12");
    expect(text("errors")).toBe("1");
    expect(text("avg_latency")).toBe("240.5 ms");
    expect(text("error_rate")).toBe("8.3%");
  });

  it("shows no stats for a saved list while MCP is off", () => {
    grid.columnDefs = [];
    renderMcp(<ServersTab gatewayId="gw-1" runtimeOff />);
    expect(grid.columnDefs.map((c) => c.headerName)).toContain("Tool");
    expect(grid.columnDefs.map((c) => c.headerName)).not.toContain(
      "Calls (30 days)",
    );
  });
});
