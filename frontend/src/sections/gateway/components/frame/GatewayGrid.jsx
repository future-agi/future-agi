import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { AgGridReact } from "ag-grid-react";

import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

import GatewayEmptyState from "../GatewayEmptyState";
import { loadColumnState, saveColumnState } from "./column-state";
import { fromControl, opensRow } from "./grid-events";

const FILL = { height: "100%", width: "100%" };

// Grid chrome the theme does not set. A header sorted with others shows its
// arrow, not AG Grid's multi-sort index ("Access 1", "Released 2"):
// engineering detail on a landing screen. The sort itself is unchanged.
const CHROME_SX = { "& .ag-sort-order": { display: "none" } };

// Column events the grid or the page makes, not the user (see remember).
const NOT_THE_USER = new Set([
  "gridOptionsChanged",
  "flex",
  "gridInitializing",
]);

// No column-menu button in the header, as on Observe's grids: the headers
// stay calm and fit. The column menu (pin, autosize, Choose Columns, reset)
// is still a right click away on any header. A page can pass
// defaultColDef={{ suppressHeaderMenuButton: false }} to show it.
const DEFAULT_COL_DEF = {
  sortable: true,
  resizable: true,
  minWidth: 80,
  flex: 1,
  suppressHeaderMenuButton: true,
};

// A gateway table: AG Grid on the Future AGI theme (theme/ag-theme.js, grey
// header row as on Observe and Datasets, light and dark). Columns sort and
// resize; with `stateKey` the user's column order, widths, visibility and
// sort are remembered in this browser, so each person shapes the one view
// like a spreadsheet. With no rows (and not loading) it shows the one
// EmptyState instead of an empty grid. A grid that opens a row (onRowClicked)
// opens it from the keyboard too: its cells take focus (Tab reaches the rows,
// the arrow keys move between them) and Enter or Space opens the focused row.
export default function GatewayGrid({
  rowData,
  columnDefs,
  loading = false,
  emptyState = undefined,
  stateKey = undefined,
  defaultColDef = undefined,
  height = undefined,
  onGridReady = undefined,
  onRowClicked = undefined,
  onCellKeyDown = undefined,
  ...gridProps
}) {
  const theme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const colDef = useMemo(
    () => ({ ...DEFAULT_COL_DEF, ...defaultColDef }),
    [defaultColDef],
  );

  const handleGridReady = useCallback(
    (event) => {
      const saved = loadColumnState(stateKey);
      if (saved) event.api.applyColumnState({ state: saved, applyOrder: true });
      onGridReady?.(event);
    },
    [onGridReady, stateKey],
  );

  // AG Grid reports a row click even when it landed on a button, link, menu
  // or input inside the row; those are the control's, not the row's.
  const handleRowClicked = useCallback(
    (event) => {
      if (!onRowClicked || fromControl(event)) return;
      onRowClicked(event);
    },
    [onRowClicked],
  );

  const handleCellKeyDown = useCallback(
    (event) => {
      onCellKeyDown?.(event);
      if (!onRowClicked || !opensRow(event)) return;
      // Space would scroll the page; here it opens the row.
      event.event.preventDefault?.();
      handleRowClicked(event);
    },
    [handleRowClicked, onCellKeyDown, onRowClicked],
  );

  // Only what the user shapes is kept. A change the page makes through its
  // column defs (AG Grid 35 reports it as "gridOptionsChanged", for example
  // Sessions hiding an empty Name) is not saved, nor are the widths the grid
  // sets itself when it lays out flex columns ("flex", on first render and
  // on every width change): either would stick as if the user had chosen
  // it, and keep a page's default sort or columns from ever changing.
  const remember = useCallback(
    (event) => {
      if (!stateKey || event.finished === false) return;
      if (NOT_THE_USER.has(event.source)) return;
      saveColumnState(stateKey, event.api.getColumnState());
    },
    [stateKey],
  );

  if (
    !loading &&
    Array.isArray(rowData) &&
    rowData.length === 0 &&
    emptyState
  ) {
    return <GatewayEmptyState {...emptyState} />;
  }

  return (
    <Box
      data-testid="gateway-grid"
      sx={{
        ...CHROME_SX,
        // A fixed height, or (none given) fill the rest of the page frame.
        ...(height === undefined
          ? { flex: "1 1 0", minHeight: 240, width: "100%" }
          : { flex: "0 0 auto", height, width: "100%" }),
      }}
    >
      <AgGridReact
        theme={theme}
        containerStyle={FILL}
        rowData={rowData}
        columnDefs={columnDefs}
        defaultColDef={colDef}
        loading={loading}
        headerHeight={40}
        rowHeight={40}
        tooltipShowDelay={400}
        suppressCellFocus={!onRowClicked}
        onGridReady={handleGridReady}
        onRowClicked={onRowClicked ? handleRowClicked : undefined}
        onCellKeyDown={
          onRowClicked || onCellKeyDown ? handleCellKeyDown : undefined
        }
        onColumnResized={remember}
        onColumnMoved={remember}
        onColumnVisible={remember}
        onSortChanged={remember}
        {...gridProps}
      />
    </Box>
  );
}

GatewayGrid.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array.isRequired,
  loading: PropTypes.bool,
  emptyState: PropTypes.object,
  stateKey: PropTypes.string,
  defaultColDef: PropTypes.object,
  height: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onGridReady: PropTypes.func,
  onRowClicked: PropTypes.func,
  onCellKeyDown: PropTypes.func,
};
