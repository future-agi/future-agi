import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { FactGrid } from "../Section";

const grid = vi.hoisted(() => ({ props: null }));
// The grid's own auto-height body, as AG Grid renders it.
vi.mock("ag-grid-react", () => ({
  AgGridReact: function MockGrid(props) {
    grid.props = props;
    return (
      <div className="ag-root ag-layout-auto-height">
        <div className="ag-center-cols-viewport" data-testid="viewport" />
      </div>
    );
  },
}));

const renderGrid = () =>
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <FactGrid
        rowData={[{ key: "a", label: "Input" }]}
        columnDefs={[{ field: "label" }]}
      />
    </ThemeProvider>,
  );

describe("FactGrid: a fact table sized to its rows", () => {
  // AG Grid gives an auto-height body 150 px at least, so three price rows
  // sat over an empty band. A fact table is exactly as tall as its rows.
  it("drops AG Grid's 150 px minimum body height", () => {
    renderGrid();
    expect(screen.getByTestId("viewport")).toHaveStyle({ minHeight: "0" });
    expect(grid.props.domLayout).toBe("autoHeight");
  });

  it("centres every cell's content in its row", () => {
    renderGrid();
    expect(grid.props.defaultColDef.cellStyle).toMatchObject({
      display: "flex",
      alignItems: "center",
    });
  });
});
