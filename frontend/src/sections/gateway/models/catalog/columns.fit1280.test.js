import { describe, expect, it } from "vitest";

import { FACET_PANEL_PX, catalogColumns } from "./columns";
import {
  effectiveColumns,
  gridBudget1440,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";

// The grid beside the facet panel at 1280 px: the 1440 px budget less the
// 160 px the window lost and the facet panel (a classic scrollbar included).
const GRID_1280 = gridBudget1440() - (1440 - 1280) - FACET_PANEL_PX;

// The columns' minimum widths added up to 964 px, so at
// 1280 px Released lay past the grid's right edge (and at 1024 px both price
// columns did), with nothing on screen saying the grid scrolls sideways.
describe("catalog columns at 1280 px (layout)", () => {
  it("fit beside the facet panel, so every default column is on screen", () => {
    const grid = { columnDefs: catalogColumns({ markupPercent: 5 }) };
    expect(minimumGridWidth(grid)).toBeLessThanOrEqual(GRID_1280);
  });

  it("keep every default column, with Model first", () => {
    const grid = { columnDefs: catalogColumns() };
    expect(effectiveColumns(grid).map((col) => col.colId)).toEqual([
      "name",
      "vendorName",
      "accessLabel",
      "context",
      "input",
      "output",
      "released",
    ]);
  });

  it("give Model the largest share of any width to spare", () => {
    const [model, ...rest] = effectiveColumns({ columnDefs: catalogColumns() });
    expect(model.flex).toBeGreaterThan(Math.max(...rest.map((c) => c.flex)));
  });
});
