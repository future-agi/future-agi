import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderMcp } from "../testing/renderMcp";
import { serverRows } from "./catalog";
import ServerDrawer from "./ServerDrawer";

// The drawer lists the server's own tools, from the same inventory as the
// server card's count, apart from Served tools: on a new organization the
// default endpoint serves none of them, and the server still shows its tools.

vi.mock("./ServedToolsPanel", () => ({
  default: () => (
    <section aria-label="Live served tools">
      <h2>Served tools (0)</h2>
    </section>
  ),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPResources: () => ({ data: [] }),
  useMCPPrompts: () => ({ data: [] }),
  useRemoveMCPServer: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useMCPToolCalls", () => ({
  useMCPToolCalls: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("../hooks/useMCPToolCallFeatures", () => ({
  useMCPToolCallFeatures: () => ({ explorer: false }),
}));
vi.mock("../hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ credentialVault: false, shown: {}, off: {} }),
}));

const [row] = serverRows(
  { servers: { issues: { url: "http://mcp.example.test/mcp" } } },
  { servers: [{ server_id: "issues", healthy: true, tool_count: 3 }] },
  [],
);
const tools = [
  { name: "issues__echo", upstream_name: "echo", server_id: "issues" },
  {
    name: "issues__get_weather",
    upstream_name: "get_weather",
    server_id: "issues",
  },
  {
    name: "issues__delete_file",
    upstream_name: "delete_file",
    server_id: "issues",
    quarantined: true,
  },
  { name: "wiki__search", upstream_name: "search", server_id: "wiki" },
];
const drawer = (props) =>
  renderMcp(
    <ServerDrawer
      row={row}
      gatewayId="default"
      onClose={() => {}}
      onEdit={() => {}}
      {...props}
    />,
  );
const inventory = () =>
  screen.getByRole("region", { name: "Tools on this server" });

describe("tools on this server", () => {
  it("lists the server's own tools, with the card's count, apart from Served tools", async () => {
    drawer({ tools });
    const list = await screen.findByRole("region", {
      name: "Tools on this server",
    });
    expect(
      within(list).getByRole("heading", {
        name: `Tools on this server (${row.toolCount})`,
      }),
    ).toBeInTheDocument();
    for (const name of ["echo", "get_weather", "delete_file"]) {
      expect(within(list).getByText(name)).toBeInTheDocument();
    }
    expect(within(list).getByText("Quarantined")).toBeInTheDocument();
    expect(within(list).queryByText("search")).not.toBeInTheDocument();
    const served = screen.getByRole("region", { name: "Live served tools" });
    expect(list.contains(served) || served.contains(list)).toBe(false);
  });

  it("shows the server's tools to members, who do not see Served tools", async () => {
    drawer({ tools, canEdit: false });
    expect(
      await screen.findByRole("heading", { name: "Tools on this server (3)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Live served tools" }),
    ).not.toBeInTheDocument();
  });

  it("says while the list loads, when it fails and when it is empty", async () => {
    const { rerender } = drawer({ tools: [], toolsLoading: true });
    expect(
      within(inventory()).getByRole("heading", {
        name: "Tools on this server",
      }),
    ).toBeInTheDocument();
    expect(
      within(inventory()).getByText("Loading this server's tools"),
    ).toBeInTheDocument();
    const props = {
      row,
      gatewayId: "default",
      onClose: () => {},
      onEdit: () => {},
    };
    rerender(<ServerDrawer {...props} tools={[]} toolsError />);
    expect(
      within(inventory()).getByText("This server's tools could not be loaded."),
    ).toBeInTheDocument();
    rerender(<ServerDrawer {...props} tools={[]} />);
    expect(
      within(inventory()).getByRole("heading", {
        name: "Tools on this server (0)",
      }),
    ).toBeInTheDocument();
    expect(
      within(inventory()).getByText("This server lists no tools yet."),
    ).toBeInTheDocument();
  });

  it("is not shown while the MCP runtime is off", async () => {
    drawer({ tools, runtimeOff: true });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Tools on this server" }),
    ).not.toBeInTheDocument();
  });
});
