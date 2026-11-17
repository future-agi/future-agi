import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ToolsGrid from "./ToolsGrid";

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
    quarantined: false,
  },
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
    quarantined: true,
  },
];

describe("ToolsGrid", () => {
  it("lists every tool with its server and state, and no invented stats", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    const rows = screen.getAllByRole("row");
    expect(
      within(rows[0]).getByText("crm-tools__find_account"),
    ).toBeInTheDocument();
    expect(within(rows[1]).getByText("Quarantined")).toBeInTheDocument();
    expect(screen.queryByText(/Error rate|Avg latency|Calls/)).toBeNull();
  });

  it("filters by server and name", () => {
    renderMcp(<ToolsGrid tools={TOOLS} isLoading={false} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search tools" }), {
      target: { value: "ticket" },
    });
    expect(screen.getAllByRole("row")).toHaveLength(1);
    expect(screen.getByText("All servers")).toBeInTheDocument();
  });

  it("explains an empty tool list", () => {
    renderMcp(<ToolsGrid tools={[]} isLoading={false} />);
    expect(screen.getByText("No tools yet")).toBeInTheDocument();
  });
});
