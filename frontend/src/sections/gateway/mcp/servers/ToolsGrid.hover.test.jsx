// A long tool or server name is cut by its column; the grid shows the full
// value on hover.
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { renderMcp } from "../testing/renderMcp";
import ToolsGrid from "./ToolsGrid";

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

const TOOL = {
  name: "acme-enterprise-sovereign-crm__find_account_by_external_reference",
  server_id: "acme-enterprise-sovereign-crm-eu-central",
  upstream_name: "find_account_by_external_reference",
  quarantined: false,
};

// What the grid shows on hover for this column and row.
const hoverText = (colId, data) => {
  const col = grid.columnDefs.find((c) => c.colId === colId);
  if (col.tooltipValueGetter)
    return col.tooltipValueGetter({
      data,
      value: data[col.field],
      colDef: col,
    });
  return col.tooltipField ? data[col.tooltipField] : undefined;
};

describe("ToolsGrid: long names on hover", () => {
  it.each([false, true])(
    "shows the full tool and server names on hover (runtime off: %s)",
    (runtimeOff) => {
      renderMcp(
        <ToolsGrid tools={[TOOL]} isLoading={false} runtimeOff={runtimeOff} />,
      );
      expect(hoverText("name", TOOL)).toBe(TOOL.name);
      expect(hoverText("server", TOOL)).toBe(TOOL.server_id);
    },
  );
});
