import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ToolsGrid from "./ToolsGrid";

// Restore (v1.58.0 mcp/MCPToolsTab.jsx): the Tools list shows each tool's
// description (and searches it), its version and a Deprecated mark, and a
// tool opens with its deprecation message and replacement, its annotations
// while MCP is on, and its input schema. The facts are the gateway's
// per-organization tool inventory's (description, input_schema, version,
// deprecated, deprecation_message, replaced_by, annotations).

const grid = vi.hoisted(() => ({ columnDefs: [] }));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  // The MCP grid stand-in, keeping the columns the page passed.
  // eslint-disable-next-line react/prop-types
  function RecordingGrid({ columnDefs, ...props }) {
    grid.columnDefs = columnDefs;
    return <MockAgGrid columnDefs={columnDefs} {...props} />;
  }
  return { AgGridReact: RecordingGrid };
});

const SCHEMA = {
  type: "object",
  properties: { account_id: { type: "string" } },
  required: ["account_id"],
};
const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
    quarantined: false,
    description: "Find an account by its name or web domain.",
    input_schema: SCHEMA,
    version: "2.1.0",
    deprecated: true,
    deprecation_message: "Use lookup_account, which also returns contacts.",
    replaced_by: "crm-tools__lookup_account",
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: true,
    },
  },
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
    quarantined: false,
  },
];

const cell = (row, colId) => row.querySelector(`[data-col="${colId}"]`);

function openTool(name) {
  fireEvent.click(screen.getByRole("button", { name: `Inspect ${name}` }));
  return screen.getByRole("dialog", { name });
}

describe("ToolsGrid: a tool's facts", () => {
  it("lists each tool's description, with a dash when its server sends none", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    const [first, second] = screen.getAllByRole("row");
    expect(cell(first, "description")).toHaveTextContent(
      "Find an account by its name or web domain.",
    );
    expect(cell(second, "description")).toHaveTextContent("—");
    const col = grid.columnDefs.find((c) => c.colId === "description");
    expect(col.headerName).toBe("Description");
    expect(col.tooltipField).toBe("description");
  });

  it("finds a tool by words in its description", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search tools" }), {
      target: { value: "web domain" },
    });
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(1);
    expect(
      within(rows[0]).getByText("crm-tools__find_account"),
    ).toBeInTheDocument();
  });

  it("marks a tool's version and a deprecated tool in its row", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    const [first, second] = screen.getAllByRole("row");
    expect(within(cell(first, "name")).getByText("v2.1.0")).toBeInTheDocument();
    expect(
      within(cell(first, "name")).getByText("Deprecated"),
    ).toBeInTheDocument();
    expect(within(second).queryByText(/^v\d/)).toBeNull();
    expect(within(second).queryByText("Deprecated")).toBeNull();
  });

  it("opens a tool with MCP on: its deprecation, annotations and input schema", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    const panel = openTool("crm-tools__find_account");
    expect(
      within(panel).getByText("Find an account by its name or web domain."),
    ).toBeInTheDocument();
    expect(within(panel).getByText("v2.1.0")).toBeInTheDocument();
    expect(within(panel).getByText("Deprecated")).toBeInTheDocument();
    expect(
      within(panel).getByText(
        "Use lookup_account, which also returns contacts.",
      ),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText("Replacement: crm-tools__lookup_account"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("Annotations")).toBeInTheDocument();
    ["Read-only", "Idempotent", "Open world"].forEach((label) =>
      expect(within(panel).getByText(label)).toBeInTheDocument(),
    );
    expect(within(panel).queryByText("Destructive")).toBeNull();
    expect(within(panel).getByText("Input schema")).toBeInTheDocument();
    expect(within(panel).getByLabelText("Input schema").textContent).toContain(
      '"account_id"',
    );
  });

  it("reads an input schema sent as JSON text or under its MCP name", () => {
    const tools = [
      {
        ...TOOLS[1],
        input_schema: JSON.stringify(SCHEMA),
      },
      {
        name: "docs__search",
        server_id: "docs",
        upstream_name: "search",
        quarantined: false,
        inputSchema: { type: "object", properties: { q: {} } },
        annotations: { destructiveHint: true },
      },
    ];
    const view = renderMcp(<ToolsGrid tools={tools} isLoading={false} />);
    let panel = openTool("support-tools__create_ticket");
    expect(within(panel).getByLabelText("Input schema").textContent).toContain(
      '"account_id"',
    );
    view.unmount();
    renderMcp(<ToolsGrid tools={tools} isLoading={false} />);
    panel = openTool("docs__search");
    expect(within(panel).getByLabelText("Input schema").textContent).toContain(
      '"q"',
    );
    expect(within(panel).getByText("Destructive")).toBeInTheDocument();
  });

  it("opens a tool its server sends none of these for with none of them", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    const panel = openTool("support-tools__create_ticket");
    expect(within(panel).queryByText("Deprecated")).toBeNull();
    expect(within(panel).queryByText("Annotations")).toBeNull();
    expect(within(panel).queryByText("Input schema")).toBeNull();
    expect(within(panel).getByText(/Server support-tools/)).toBeInTheDocument();
  });

  it("with MCP off opens the saved copy, with no live annotation marks", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} runtimeOff />);
    const panel = openTool("crm-tools__find_account");
    expect(within(panel).getByText("Annotations")).toBeInTheDocument();
    expect(within(panel).queryByText("Read-only")).toBeNull();
    expect(within(panel).queryByText("Open world")).toBeNull();
  });

  it("keeps a slot for per-tool stats columns before the state", () => {
    const stats = [{ colId: "calls", headerName: "Calls", field: "calls" }];
    renderMcp(
      <ToolsGrid tools={TOOLS} isLoading={false} statsColumns={stats} />,
    );
    const ids = grid.columnDefs.map((c) => c.colId);
    expect(ids).toContain("calls");
    expect(ids.indexOf("calls")).toBeLessThan(ids.indexOf("state"));
    expect(ids.indexOf("calls")).toBeGreaterThan(ids.indexOf("description"));
  });

  it("adds no stats column of its own", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    expect(grid.columnDefs.map((c) => c.colId)).toEqual([
      "name",
      "server",
      "upstream",
      "description",
      "state",
    ]);
  });
});
