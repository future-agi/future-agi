import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";

import RequestTable from "../RequestTable";
import { BUILTIN_COLUMNS, resolveColumns } from "../columns/columnModel";
import { mountedGrids } from "./fakeAgGrid";

// The Logs columns' minimums fit the grid at 1440 px only; at 1280 and 1024
// px they run past its right edge, so the time and the far columns can't be
// read together. From tablet width up Timestamp stays pinned at the left,
// at a fixed width, and the grid shades the right edge while columns lie past
// it. A phone pins nothing. Timestamp is first in every layout (the
// Columns picker keeps it there), so pinning it never reorders a layout.

// The window's width, as a browser reports it (jsdom: 1024).
const setWindowWidth = (width) =>
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
vi.mock("ag-grid-react", () => import("./fakeAgGrid"));
vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: {
      results: [
        {
          id: "a",
          request_id: "r-a",
          started_at: "2026-11-11T10:00:00Z",
          cost: "0.000012",
        },
      ],
      count: 1,
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

const TIME = "builtin:startedAt";

const renderTable = (props = {}) => {
  render(
    <RequestTable
      filters={{}}
      setFilter={vi.fn()}
      setFilters={vi.fn()}
      onSelectLog={vi.fn()}
      {...props}
    />,
  );
  const [grid] = mountedGrids();
  return grid;
};

const colDef = (grid, colId) => grid.columnDefs.find((c) => c.colId === colId);

// The grid's centre columns at their 1024 px minimum, in a 790 px viewport.
const narrowApi = (left) => ({
  getHorizontalPixelRange: () => ({ left, right: left + 790 }),
  getDisplayedCenterColumns: () => [{ getActualWidth: () => 1016 }],
  getColumnState: () => [],
});

describe("Logs table at narrow widths: Timestamp pinned, the edge shaded", () => {
  it("pins Timestamp at the left, at a fixed width, from tablet width up", () => {
    setWindowWidth(1024);
    const grid = renderTable();
    const time = colDef(grid, TIME);
    expect(time).toMatchObject({ pinned: "left", flex: 0 });
    expect(time.width).toBeGreaterThanOrEqual(time.minWidth);
    for (const column of grid.columnDefs.filter((c) => c.colId !== TIME)) {
      expect(column.pinned).toBeUndefined();
    }
  });

  it("pins nothing on a phone", () => {
    setWindowWidth(390);
    const grid = renderTable();
    const time = colDef(grid, TIME);
    expect(time.pinned).toBeNull();
    expect(time.flex).toBeGreaterThan(0);
    setWindowWidth(1024);
  });

  it("shades the right edge while columns lie past it, and clears it at the end", () => {
    setWindowWidth(1024);
    const grid = renderTable();
    act(() => grid.onGridSizeChanged({ api: narrowApi(0) }));
    expect(screen.getByTestId("gateway-grid-more-right")).toBeInTheDocument();
    act(() => mountedGrids()[0].onBodyScroll({ api: narrowApi(226) }));
    expect(screen.queryByTestId("gateway-grid-more-right")).toBeNull();
  });

  it("keeps a saved layout's order: Timestamp first and pinned, the rest as saved", () => {
    setWindowWidth(1024);
    const { columns } = resolveColumns({
      config: {
        v: 1,
        columns: [
          { id: TIME },
          ...BUILTIN_COLUMNS.slice(1)
            .reverse()
            .map((c) => ({ id: c.id })),
        ],
        hidden: [],
      },
      declarations: [],
      status: "success",
    });
    const grid = renderTable({ columns });
    expect(grid.columnDefs.map((c) => c.colId)).toEqual(
      columns.map((c) => c.id),
    );
    expect(colDef(grid, TIME).pinned).toBe("left");
  });
});
