import React, { forwardRef } from "react";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { GatewayGrid } from "..";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

function renderGrid(props) {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <GatewayGrid
        rowData={[{ id: "r1" }]}
        columnDefs={[{ field: "id" }]}
        {...props}
      />
    </ThemeProvider>,
  );
}

// A keyboard event the way AG Grid reports it from a focused cell.
function cellKey(key, target = document.createElement("div")) {
  const preventDefault = vi.fn();
  return {
    event: { key, target, preventDefault },
    data: { id: "r1" },
    preventDefault,
  };
}

// Every gateway grid that opens a drawer on a
// row click opens it from the keyboard too, as the Catalog alone did.
describe("GatewayGrid keyboard", () => {
  afterEach(() => {
    grid.props = null;
  });

  it("with onRowClicked, cells are focusable and Enter calls onRowClicked", () => {
    const onRowClicked = vi.fn();
    renderGrid({ onRowClicked });
    expect(grid.props.suppressCellFocus).toBe(false);
    grid.props.onCellKeyDown(cellKey("Enter"));
    expect(onRowClicked).toHaveBeenCalledTimes(1);
    expect(onRowClicked.mock.calls[0][0].data).toEqual({ id: "r1" });
  });

  it("Space opens the row too, without scrolling the page", () => {
    const onRowClicked = vi.fn();
    renderGrid({ onRowClicked });
    const event = cellKey(" ");
    grid.props.onCellKeyDown(event);
    expect(onRowClicked).toHaveBeenCalledTimes(1);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  it("other keys move focus and open nothing", () => {
    const onRowClicked = vi.fn();
    renderGrid({ onRowClicked });
    grid.props.onCellKeyDown(cellKey("ArrowDown"));
    grid.props.onCellKeyDown(cellKey("Tab"));
    expect(onRowClicked).not.toHaveBeenCalled();
  });

  it("Enter on a button inside a cell belongs to the button, not the row", () => {
    const onRowClicked = vi.fn();
    renderGrid({ onRowClicked });
    const button = document.createElement("button");
    document.body.appendChild(button);
    grid.props.onCellKeyDown(cellKey("Enter", button));
    expect(onRowClicked).not.toHaveBeenCalled();
    button.remove();
  });

  it("still calls a page's own onCellKeyDown", () => {
    const onRowClicked = vi.fn();
    const onCellKeyDown = vi.fn();
    renderGrid({ onRowClicked, onCellKeyDown });
    grid.props.onCellKeyDown(cellKey("Enter"));
    expect(onCellKeyDown).toHaveBeenCalledTimes(1);
    expect(onRowClicked).toHaveBeenCalledTimes(1);
  });

  it("without onRowClicked, cell focus stays suppressed", () => {
    renderGrid({});
    expect(grid.props.suppressCellFocus).toBe(true);
    expect(grid.props.onCellKeyDown).toBeUndefined();
  });
});

// A column change the page makes through its column defs (Sessions hides an
// empty Name) is not the user's choice: saving it would freeze it, and the
// page could never show the column again on its own. AG Grid 35 reports
// those with source "gridOptionsChanged".
describe("GatewayGrid remembered column state", () => {
  const STATE = [{ colId: "name", hide: true }];
  const event = (source) => ({
    source,
    api: { getColumnState: () => STATE },
  });

  afterEach(() => window.localStorage.clear());

  it("does not save a column change the column defs made", () => {
    renderGrid({ stateKey: "t-defs" });
    grid.props.onColumnVisible(event("gridOptionsChanged"));
    expect(window.localStorage.getItem("gateway-grid:t-defs")).toBeNull();
  });

  // AG Grid sizes flex columns when the grid first lays out (and whenever
  // its width changes) and reports it as a "flex" resize. Saving that would
  // keep every default (sort, hidden columns) as if the user had chosen it.
  it("does not save the widths the grid's own flex layout set", () => {
    renderGrid({ stateKey: "t-flex" });
    grid.props.onColumnResized({ ...event("flex"), finished: true });
    grid.props.onColumnResized({
      ...event("gridInitializing"),
      finished: true,
    });
    expect(window.localStorage.getItem("gateway-grid:t-flex")).toBeNull();
  });

  it("saves a width the user dragged", () => {
    renderGrid({ stateKey: "t-drag" });
    grid.props.onColumnResized({
      ...event("uiColumnResized"),
      finished: true,
    });
    expect(
      JSON.parse(window.localStorage.getItem("gateway-grid:t-drag")),
    ).toEqual(STATE);
  });

  it("saves a change the user made", () => {
    renderGrid({ stateKey: "t-user" });
    grid.props.onColumnVisible(event("columnMenu"));
    expect(
      JSON.parse(window.localStorage.getItem("gateway-grid:t-user")),
    ).toEqual(STATE);
  });
});
