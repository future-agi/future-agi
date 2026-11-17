import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderMcp } from "../testing/renderMcp";
import ServersTab from "./ServersTab";
import ServerCatalog from "./ServerCatalog";
import { serverRows } from "./catalog";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../../testing/adminOnly";

// Restore (v1.58.0 mcp/MCPServersTab.jsx): each server card carries Edit and
// Remove itself, as well as the drawer's. They write the org's gateway
// config, so they are Owner or Admin only, disabled with the reason for
// everyone else, as the drawer's are.

const { org, removeMutate, updateMutate } = vi.hoisted(() => ({
  org: { level: 8 },
  removeMutate: vi.fn(),
  updateMutate: vi.fn(),
}));

// The drawer's own reads (served tools, keys) answer nothing here.
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: {
    get: vi.fn().mockRejectedValue(new Error("Unexpected request")),
    post: vi.fn(),
  },
}));
vi.mock("ag-grid-react", async () => ({
  AgGridReact: (await import("../testing/agGridMock")).MockAgGrid,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
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
          search: {
            url: "https://mcp.example.com/search/mcp",
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
      enabled: true,
      features: {},
      servers: [
        { server_id: "github", healthy: true, tool_count: 1 },
        { server_id: "search", healthy: true, tool_count: 0 },
      ],
    },
  }),
  useMCPTools: () => ({ data: [], isLoading: false }),
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: removeMutate, isPending: false }),
  useUpdateMCPServer: () => ({ mutate: updateMutate, isPending: false }),
}));
vi.mock("../hooks/useMCPCredentials", () => ({
  useMCPCredentials: () => ({ data: { rows: [], count: 0 } }),
}));
vi.mock("../hooks/useMCPToolCalls", () => ({
  useMCPToolCalls: () => ({ data: { pages: [] }, isLoading: false }),
}));

const editButton = (id) => screen.getByRole("button", { name: `Edit ${id}` });
const removeButton = (id) =>
  screen.getByRole("button", { name: `Remove ${id}` });

beforeEach(() => {
  org.level = 8;
  removeMutate.mockReset();
  updateMutate.mockReset();
});

describe("ServersTab: Edit and Remove on the server card", () => {
  it.each(ADMIN_LEVELS)("gives %s both on every card", (_, level) => {
    org.level = level;
    renderMcp(<ServersTab gatewayId="gw-1" />);
    for (const id of ["github", "search"]) {
      expectEditable(editButton(id));
      expectEditable(removeButton(id));
    }
  });

  it.each(NOT_ADMIN_LEVELS)("disables both for %s, saying why", (_, level) => {
    org.level = level;
    renderMcp(<ServersTab gatewayId="gw-1" />);
    expectAdminOnly(editButton("github"));
    expectAdminOnly(removeButton("github"));
    fireEvent.click(editButton("github"));
    fireEvent.click(removeButton("github"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(removeMutate).not.toHaveBeenCalled();
  });

  it("edits a server from its card, without opening the drawer", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    await user.click(editButton("github"));
    expect(await screen.findByText("Edit github")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "github" })).toBeNull();
  });

  it("removes a server from its card after confirming", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    await user.click(removeButton("search"));
    const confirm = await screen.findByRole("dialog", {
      name: "Remove MCP server",
    });
    expect(confirm).toHaveTextContent('Remove "search"');
    await user.click(
      within(confirm).getByRole("button", { name: "Remove server" }),
    );
    expect(removeMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      serverId: "search",
    });
  });

  it("removes nothing when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    await user.click(removeButton("github"));
    const confirm = await screen.findByRole("dialog", {
      name: "Remove MCP server",
    });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(removeMutate).not.toHaveBeenCalled();
  });

  it("still opens the drawer from the card, which keeps its own pair", async () => {
    const user = userEvent.setup();
    renderMcp(<ServersTab gatewayId="gw-1" />);
    await user.click(screen.getByRole("button", { name: /^github\b/ }));
    const drawer = await screen.findByRole("dialog", { name: "github" });
    for (const name of ["Edit", "Remove"]) {
      expectEditable(within(drawer).getByRole("button", { name }));
    }
  });
});

describe("ServerCatalog without card actions", () => {
  it("shows no Edit or Remove when the caller passes none", () => {
    const rows = serverRows(
      { servers: { github: { url: "https://mcp.example.com/github/mcp" } } },
      { servers: [{ server_id: "github", healthy: true, tool_count: 1 }] },
    );
    renderMcp(<ServerCatalog rows={rows} onOpen={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
  });
});
