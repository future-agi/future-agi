import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomTraceRenderer from "../CustomTraceRenderer";

// The row-hover funnel (quick filter) on the observe grids' text cells sits
// one level under side drawers on the Traces and Spans grids, for the name
// cell and the other text cells (DefaultCell, reached through
// CustomTraceRenderer) alike, so a cell still hovered after the click that
// opens the trace drawer never floats its funnel over the drawer. The cells
// are rendered through
// CustomTraceRenderer, the renderer the grids use, so the flag is followed
// from the grid's cell params to the funnel.

// Partial mock: the module stays real, only the hook is swapped, so a trace
// or span id click is a spy instead of a route change and a store write.
const nav = vi.hoisted(() => ({
  handleTraceClick: vi.fn(),
  handleSpanClick: vi.fn(),
}));
vi.mock("../useNavigationHandlers", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigationHandlers: () => nav,
}));

const MODEL_COLUMN = { id: "model", name: "Model" };
const MODEL = "gpt-4o-mini";
const ROW = {
  project_id: "project-1",
  trace_id: "trace-1",
  span_id: "span-1",
  model: MODEL,
};
// The test render uses MUI's default stacking levels.
const { zIndex } = createTheme();

const GRIDS = [
  ["Traces", "trace"],
  ["Spans", "span"],
];

// The props the grids hand a cell renderer: colDef, value, row data, grid
// context, and the defaultColDef cellRendererParams (applyQuickFilters and,
// on the Traces and Spans grids, quickFilterBelowDrawers).
const renderCell = ({
  column = MODEL_COLUMN,
  value = MODEL,
  entityType = "trace",
  ...cellRendererParams
} = {}) => {
  const applyQuickFilters = vi.fn();
  render(
    <CustomTraceRenderer
      colDef={{ context: { sourceColumn: column } }}
      value={value}
      data={ROW}
      context={{ entityType }}
      applyQuickFilters={applyQuickFilters}
      {...cellRendererParams}
    />,
  );
  return { applyQuickFilters };
};

const openFunnel = async (text) => {
  fireEvent.mouseOver(screen.getByText(text));
  return screen.findByRole("button", { name: "Filter by this value" });
};

const popperOf = (funnel) => funnel.closest('[role="tooltip"]');

describe("observe grid text cell funnel layer", () => {
  beforeEach(() => {
    nav.handleTraceClick.mockClear();
    nav.handleSpanClick.mockClear();
  });

  it.each(GRIDS)(
    "stacks a hovered Model cell's funnel beneath side drawers on the %s grid",
    async (_grid, entityType) => {
      renderCell({ entityType, quickFilterBelowDrawers: true });

      const popper = popperOf(await openFunnel(MODEL));
      expect(popper).not.toBeNull();
      expect(popper).toHaveStyle({ zIndex: String(zIndex.drawer - 1) });
    },
  );

  it.each([
    [
      "Trace Id",
      "trace drawer",
      "trace",
      { id: "trace_id", name: "Trace Id" },
      "trace-1",
      "handleTraceClick",
    ],
    [
      "Span Id",
      "span drawer",
      "span",
      { id: "span_id", name: "Span Id" },
      "span-1",
      "handleSpanClick",
    ],
  ])(
    "keeps the funnel beneath drawers after a click on a %s cell opens the %s",
    async (_label, _drawer, entityType, column, value, opener) => {
      renderCell({ entityType, column, value, quickFilterBelowDrawers: true });
      await openFunnel(value);

      // The click that opens the drawer; the pointer stays on the cell.
      fireEvent.click(screen.getByText(value));
      expect(nav[opener]).toHaveBeenCalledWith(value);

      // The funnel is still up, and it now sits under the open drawer.
      const funnel = screen.getByRole("button", {
        name: "Filter by this value",
      });
      expect(popperOf(funnel)).toHaveStyle({
        zIndex: String(zIndex.drawer - 1),
      });
    },
  );

  it("keeps the tooltip stacking level on grids that do not ask for it", async () => {
    // Grids that reuse this renderer without the flag (the annotation queue's
    // Add items drawer, the prompt Metrics and Linked traces tables) keep the
    // funnel at the tooltip level, so a panel they sit in never covers it.
    renderCell();

    const popper = popperOf(await openFunnel(MODEL));
    expect(popper).toHaveStyle({ zIndex: String(zIndex.tooltip) });
  });

  it("still applies the quick filter for this value from the funnel", async () => {
    const { applyQuickFilters } = renderCell({ quickFilterBelowDrawers: true });

    fireEvent.click(await openFunnel(MODEL));

    expect(applyQuickFilters).toHaveBeenCalledTimes(1);
    expect(applyQuickFilters).toHaveBeenCalledWith({
      col: MODEL_COLUMN,
      value: MODEL,
      filterAnchor: { top: expect.any(Number), left: expect.any(Number) },
    });
    // The funnel click filters; it does not open a drawer.
    expect(nav.handleTraceClick).not.toHaveBeenCalled();
    expect(nav.handleSpanClick).not.toHaveBeenCalled();
  });
});
