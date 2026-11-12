import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

import RequestTable from "../RequestTable";
import { BUILTIN_COLUMNS } from "../columns/columnModel";
import { GATEWAY_COLUMNS } from "../columns/gatewayColumns";
import { mountedGrids } from "./fakeAgGrid";
import {
  CELL_PADDING_PX,
  CELL_TEXT_PX,
  columnOf,
  cutHeaders,
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

const renderTable = (columns = BUILTIN_COLUMNS) => {
  render(
    <RequestTable
      filters={{}}
      setFilter={vi.fn()}
      setFilters={vi.fn()}
      onSelectLog={vi.fn()}
      columns={columns}
    />,
  );
  const [grid] = mountedGrids();
  return grid;
};

describe("Logs grid widths", () => {
  it("never cuts off a sub-cent cost: the Cost column is at least as wide as $0.000999", () => {
    const cost = columnOf(renderTable(), "builtin:cost");
    expect(cost.minWidth).toBeGreaterThanOrEqual(
      CELL_TEXT_PX["$0.000999"] + CELL_PADDING_PX,
    );
  });

  // The Logs grid shows no column-menu button: its Columns picker owns the
  // columns. A header needs its label, the padding and, once sorted, the arrow.
  it("cuts off no default header at its minimum width", () => {
    expect(cutHeaders(renderTable())).toEqual([]);
  });

  it("cuts off no Agent & billing header either", () => {
    expect(
      cutHeaders(renderTable([BUILTIN_COLUMNS[0], ...GATEWAY_COLUMNS])),
    ).toEqual([]);
  });

  it("fits its ten default columns at 1440 px without scrolling sideways", () => {
    expect(minimumGridWidth(renderTable())).toBeLessThanOrEqual(
      gridBudget1440(),
    );
  });
});
