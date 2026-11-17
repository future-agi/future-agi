import React from "react";
import { GlobalStyles } from "@mui/material";
import { HAS_SELECTION_CLASS, HOVER_SELECT_CLASS } from "./useHoverSelection";

const H = `.${HOVER_SELECT_CLASS}:not(.${HAS_SELECTION_CLASS})`;

/**
 * OneViewStyles — the global CSS of the one-view primitives, mounted once in
 * the dashboard layout:
 * - hover selection (§3.10): checkboxes fade in on row hover / focus, and on
 *   every row once one is selected (AG Grid and MUI DataGrid);
 * - header `⋮` buttons (`.fa-header-menu-btn`) appear on header hover or
 *   focus.
 * Hidden checkboxes keep their box (opacity, not display) so nothing moves.
 */
export default function OneViewStyles() {
  return (
    <GlobalStyles
      styles={(theme) => ({
        // AG Grid rows
        [`${H} .ag-row:not(.ag-row-hover):not(.ag-row-focus):not(.ag-row-selected):not(:focus-within) .ag-selection-checkbox`]:
          { opacity: 0 },
        [`${H} .ag-header:not(:hover):not(:focus-within) .ag-header-select-all`]:
          { opacity: 0 },
        // MUI DataGrid rows (DataTable selectOnHover). The checkbox fades,
        // not its cell: the cell keeps its bottom divider, so a row's line
        // spans the same width as the header's.
        [`${H} .MuiDataGrid-row:not(:hover):not(.Mui-selected):not(:focus-within):not(.row-active) .MuiDataGrid-cellCheckbox > *`]:
          { opacity: 0 },
        [`${H} .MuiDataGrid-columnHeaders:not(:hover):not(:focus-within) .MuiDataGrid-columnHeaderCheckbox > *`]:
          { opacity: 0 },
        [`.${HOVER_SELECT_CLASS} .ag-selection-checkbox, .${HOVER_SELECT_CLASS} .MuiDataGrid-cellCheckbox > *, .${HOVER_SELECT_CLASS} .ag-header-select-all, .${HOVER_SELECT_CLASS} .MuiDataGrid-columnHeaderCheckbox > *`]:
          { transition: "opacity 120ms ease" },
        // Header ⋮ (HeaderMenuButton): visible on header hover / focus, or
        // while its menu is open.
        ".fa-header-menu-btn": { opacity: 0, transition: "opacity 120ms ease" },
        [`.MuiDataGrid-columnHeader:hover .fa-header-menu-btn,
          .MuiDataGrid-columnHeader:focus-within .fa-header-menu-btn,
          .fa-header-cell:hover .fa-header-menu-btn,
          .fa-header-cell:focus-within .fa-header-menu-btn,
          .fa-header-menu-btn[aria-expanded="true"],
          .fa-header-menu-btn:focus-visible`]: { opacity: 1 },
        "@media (hover: none)": { ".fa-header-menu-btn": { opacity: 1 } },
        // IT-R1 G5 P11: frozen (pinned-left) columns end at a visible line,
        // so it is clear what stays put when the grid scrolls sideways.
        [`.ag-pinned-left-header:not(.ag-hidden),
          .ag-pinned-left-cols-container:not(.ag-hidden),
          .ag-pinned-left-floating-top:not(.ag-hidden),
          .ag-pinned-left-floating-bottom:not(.ag-hidden)`]: {
          borderRight: `2px solid ${theme.palette.divider}`,
        },
      })}
    />
  );
}
