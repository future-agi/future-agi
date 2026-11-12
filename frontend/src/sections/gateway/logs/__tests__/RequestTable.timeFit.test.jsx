import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

import RequestTable from "../RequestTable";
import { mountedGrids } from "./fakeAgGrid";
import {
  CELL_PADDING_PX,
  columnOf,
  gridBudget1440,
  minimumGridWidth,
} from "./gridFit";

vi.mock("ag-grid-react", () => import("./fakeAgGrid"));
vi.mock("../hooks/useRequestLogs", () => ({
  default: () => ({
    data: { results: [{ id: "a", cost: "0.000005" }], count: 1 },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

// A log time ("Nov 11 14:08:08.545") is 126 px wide in the Logs grid
// (its cell's scrollWidth in Chromium on the e2e stack), and the Timestamp
// column at its 156 px minimum left it 124 px, so it ended in an ellipsis at
// 1280 and 1024. 6 px more covers wider month names and subpixel rounding.
const LOG_TIME_PX = 126 + 6;

const renderTable = () => {
  render(
    <RequestTable
      filters={{}}
      setFilter={vi.fn()}
      setFilters={vi.fn()}
      onSelectLog={vi.fn()}
    />,
  );
  const [grid] = mountedGrids();
  return grid;
};

describe("Logs grid, Timestamp column", () => {
  it("keeps a whole log time, milliseconds included, at its minimum width", () => {
    const time = columnOf(renderTable(), "builtin:startedAt");
    expect(time.minWidth).toBeGreaterThanOrEqual(LOG_TIME_PX + CELL_PADDING_PX);
  });

  it("still fits the default columns at 1440 px without scrolling sideways", () => {
    expect(minimumGridWidth(renderTable())).toBeLessThanOrEqual(
      gridBudget1440(),
    );
  });
});
