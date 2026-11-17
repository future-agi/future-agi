import React from "react";
import { describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { fireEvent, render, screen } from "src/utils/test-utils";
import NameCell from "../NameCell";

// G3: the Trace Name / Span Name cells of the observe grids.
// 1. Long names were cut mid-letter ('long-research-agent-run-with-a-v') with
//    no ellipsis: the text sat directly in a flex container, which never
//    draws one. The name now sits in its own block that ends in "…".
// 2. The hover funnel (quick filter) was drawn over the end of the name. The
//    name now stops 40px short of the cell's right edge, clear of the funnel.
// 3. The funnel is a tooltip popper, so it stacked above the open trace
//    drawer (seen at 1024 over the Trace Tree at x≈481). On the page grids it
//    now stacks one level under side drawers.

const LONG_NAME =
  "long-research-agent-run-with-a-very-long-trace-name-1280-mwbk8a6g";
const SOURCE_COLUMN = { id: "trace_name", name: "Trace Name" };
// The test render uses MUI's default stacking levels.
const { zIndex } = createTheme();

const renderCell = (props = {}) => {
  const applyQuickFilters = vi.fn();
  render(
    <NameCell
      value={LONG_NAME}
      colDef={{ context: { sourceColumn: SOURCE_COLUMN } }}
      applyQuickFilters={applyQuickFilters}
      {...props}
    />,
  );
  return { applyQuickFilters };
};

const openFunnel = async () => {
  fireEvent.mouseOver(screen.getByText(LONG_NAME));
  return screen.findByRole("button", { name: "Filter by this value" });
};

describe("observe grid name cell layout (G3)", () => {
  it("puts the name in its own clipping block so a long name ends in an ellipsis", () => {
    renderCell();

    const text = screen.getByText(LONG_NAME);
    // The element holding the text is a block, not the flex container.
    expect(text).not.toHaveStyle({ display: "flex" });
    expect(text).toHaveStyle({
      display: "block",
      minWidth: "0",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
    // The full name stays in the cell; only its display is clipped.
    expect(text.textContent).toBe(LONG_NAME);
  });

  it("keeps the name 40px clear of the cell's right edge, where the funnel sits", () => {
    renderCell();

    const anchor = screen.getByText(LONG_NAME).parentElement;
    expect(anchor).toHaveStyle({
      display: "flex",
      alignItems: "center",
      paddingRight: "40px",
    });
  });

  it("stacks the funnel beneath side drawers on the page grids", async () => {
    renderCell({ quickFilterBelowDrawers: true });

    const funnel = await openFunnel();
    const popper = funnel.closest('[role="tooltip"]');
    expect(popper).not.toBeNull();
    expect(popper).toHaveStyle({ zIndex: String(zIndex.drawer - 1) });
  });

  it("keeps the tooltip stacking level when the grid does not ask for it", async () => {
    // Grids that sit inside a drawer (the annotation queue's add-items panel)
    // do not pass the flag, so their own panel never covers the funnel.
    renderCell();

    const funnel = await openFunnel();
    expect(funnel.closest('[role="tooltip"]')).toHaveStyle({
      zIndex: String(zIndex.tooltip),
    });
  });

  it("still applies the quick filter for this name from the funnel", async () => {
    const { applyQuickFilters } = renderCell({ quickFilterBelowDrawers: true });

    fireEvent.click(await openFunnel());

    expect(applyQuickFilters).toHaveBeenCalledWith({
      col: SOURCE_COLUMN,
      value: LONG_NAME,
      filterAnchor: {},
    });
  });
});
