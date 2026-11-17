import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import ToolsGrid from "./ToolsGrid";
import { TOOL_STATS_COLUMNS, formatLastCalled } from "./toolStats";

// Restore (v1.58.0 mcp/MCPToolsTab.jsx): the Tools list shows each tool's
// Calls, Errors, Avg latency, Error rate and Last called again, now from the
// organization's stored tool calls of the last 30 days (each tool's `stats`
// on the mcp-tools read), never the gateway's counters since it started.

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

const NOW = new Date("2026-11-13T12:00:00Z");

const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
    quarantined: false,
    stats: {
      call_count: 1234,
      error_count: 3,
      avg_latency_ms: 30,
      error_rate: 0.0024,
      last_called: "2026-11-13T09:30:00+00:00",
      window_days: 30,
    },
  },
  {
    name: "crm-tools__merge_accounts",
    server_id: "crm-tools",
    upstream_name: "merge_accounts",
    quarantined: false,
    stats: {
      call_count: 0,
      error_count: 0,
      avg_latency_ms: null,
      error_rate: 0,
      last_called: null,
      window_days: 30,
    },
  },
  // A list saved before the read sent stats.
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
    quarantined: false,
  },
];

const STATS_COLS = [
  "calls",
  "errors",
  "avg_latency",
  "error_rate",
  "last_called",
];

const cell = (row, colId) => row.querySelector(`[data-col="${colId}"]`);
const statCells = (row) =>
  STATS_COLS.map((colId) => cell(row, colId).textContent);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ToolsGrid: per-tool call stats", () => {
  it("puts the five stats columns between Description and State", () => {
    renderMcp(
      <ToolsGrid
        tools={TOOLS}
        isLoading={false}
        statsColumns={TOOL_STATS_COLUMNS}
      />,
    );
    expect(grid.columnDefs.map((c) => c.headerName)).toEqual([
      "Tool",
      "Server",
      "Upstream name",
      "Description",
      "Calls (30 days)",
      "Errors",
      "Avg latency",
      "Error rate",
      "Last called",
      "State",
    ]);
  });

  it("shows a tool's calls, errors, average latency, error rate and last call", () => {
    renderMcp(
      <ToolsGrid
        tools={TOOLS}
        isLoading={false}
        statsColumns={TOOL_STATS_COLUMNS}
      />,
    );
    const [called] = screen.getAllByRole("row");
    expect(statCells(called)).toEqual([
      "1,234",
      "3",
      "30.0 ms",
      "0.2%",
      "2h ago",
    ]);
  });

  it("shows zeros and dashes for a tool with no calls, dashes with no stats", () => {
    renderMcp(
      <ToolsGrid
        tools={TOOLS}
        isLoading={false}
        statsColumns={TOOL_STATS_COLUMNS}
      />,
    );
    const [, idle, unknown] = screen.getAllByRole("row");
    expect(statCells(idle)).toEqual(["0", "0", "—", "—", "—"]);
    expect(statCells(unknown)).toEqual(["—", "—", "—", "—", "—"]);
  });

  it("names the window on every stats column, never a gateway restart", () => {
    for (const col of TOOL_STATS_COLUMNS) {
      expect(col.headerTooltip).toMatch(/^Last 30 days/);
      expect(`${col.headerName} ${col.headerTooltip}`).not.toMatch(/restart/i);
    }
  });

  it("reads the last call as minutes, hours, then a date", () => {
    const now = NOW.getTime();
    expect(formatLastCalled("2026-11-13T11:59:30Z", now)).toBe("just now");
    expect(formatLastCalled("2026-11-13T11:15:00Z", now)).toBe("45m ago");
    expect(formatLastCalled("2026-11-12T13:00:00Z", now)).toBe("23h ago");
    expect(formatLastCalled("2026-11-01T08:00:00Z", now)).toBe("11/1/2026");
    expect(formatLastCalled(null, now)).toBe("—");
    expect(formatLastCalled("not a time", now)).toBe("—");
  });
});
