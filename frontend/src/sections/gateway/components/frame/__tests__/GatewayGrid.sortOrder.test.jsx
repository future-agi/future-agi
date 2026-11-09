import React, { forwardRef } from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { GatewayGrid } from "..";

// AG Grid's multi-sort index, as it renders it in a sorted header.
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(_props, _ref) {
    return (
      <div className="ag-header-cell">
        <span className="ag-sort-indicator-icon ag-sort-order">1</span>
        <span className="ag-sort-indicator-icon ag-sort-ascending-icon" />
      </div>
    );
  }),
}));

// The catalog's headers read "Access 1 ↑" and
// "Released 2 ↓": AG Grid's multi-sort index, engineering detail on the
// landing screen. The arrows say how a column sorts; the index is hidden.
describe("GatewayGrid sorted headers", () => {
  it("show the sort arrow, not AG Grid's multi-sort index", () => {
    const { container } = render(
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <GatewayGrid rowData={[{ id: 1 }]} columnDefs={[{ field: "id" }]} />
      </ThemeProvider>,
    );
    expect(screen.getByText("1")).toHaveStyle({ display: "none" });
    expect(container.querySelector(".ag-sort-ascending-icon")).not.toHaveStyle({
      display: "none",
    });
  });
});
