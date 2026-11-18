/* eslint-disable react/prop-types */
import { useCallback, useEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import {
  DataGrid,
  gridVisibleColumnDefinitionsSelector,
  useGridApiContext,
  useGridSelector,
} from "@mui/x-data-grid";
import { Box, Skeleton, useTheme } from "@mui/material";
import EmptyContent from "src/components/empty-content";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";

/**
 * DataTable — thin wrapper on MUI X DataGrid (already in the project).
 * Server-side pagination, sorting, selection. No virtualization white flash.
 */
// First-load skeleton (no rows yet): one bar per visible column, at that column's real
// width, under the real headers (the checkbox column gets none).
const SKELETON_ROW_COUNT = 8;
function SkeletonRowsOverlay({ rowHeight }) {
  const apiRef = useGridApiContext();
  const columns = useGridSelector(apiRef, gridVisibleColumnDefinitionsSelector);
  return (
    <Box
      role="progressbar"
      aria-label="Loading"
      aria-busy="true"
      sx={{ height: 1, bgcolor: "background.paper", overflow: "hidden" }}
    >
      {Array.from({ length: SKELETON_ROW_COUNT }, (_, row) => (
        <Box
          // eslint-disable-next-line react/no-array-index-key
          key={row}
          sx={{
            display: "flex",
            alignItems: "center",
            height: rowHeight,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          {columns.map((col, i) => (
            <Box
              key={col.field}
              sx={{ width: col.computedWidth, flexShrink: 0, px: "10px" }}
            >
              {col.field !== "__check__" && (
                <Skeleton
                  variant="rounded"
                  height={10}
                  width={`${[70, 45, 55, 35][(i + row) % 4]}%`}
                />
              )}
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
}
SkeletonRowsOverlay.propTypes = { rowHeight: PropTypes.number };

export default function DataTable({
  columns,
  data,
  isLoading = false,
  rowCount = 0,
  sorting,
  onSortingChange,
  rowSelection,
  onRowSelectionChange,
  onRowClick,
  getRowId,
  columnVisibility,
  onColumnVisibilityChange,
  emptyMessage = "No data found",
  rowHeight = ROW_HEIGHT.default,
  headerHeight = TABLE_HEADER_HEIGHT.default,
  enableSelection = false,
  // Row the caller is showing elsewhere (a detail panel, a drawer): it is
  // highlighted and kept in view as keyboard navigation moves it.
  activeRowId,
}) {
  const theme = useTheme();
  const containerRef = useRef(null);

  useEffect(() => {
    if (activeRowId == null) return;
    const rows = containerRef.current?.querySelectorAll("[data-id]") || [];
    Array.from(rows)
      .find((el) => el.dataset.id === String(activeRowId))
      ?.scrollIntoView?.({ block: "nearest" });
  }, [activeRowId]);

  // Convert our column format → MUI DataGrid columns
  const muiColumns = useMemo(() => {
    return columns.map((col) => {
      const field = col.id || col.accessorKey;
      const dataKey = col.accessorKey || col.id;
      const muiCol = {
        field,
        headerName: col.header,
        sortable: col.enableSorting !== false,
        disableColumnMenu: true,
        resizable: true,
      };
      if (dataKey !== field) {
        muiCol.valueGetter = (params) => params.row[dataKey];
      }

      // Opt-in custom header node. `headerName` stays a string (aria-label /
      // sort tooltip); a column that needs a rich header supplies renderHeader.
      if (col.renderHeader) {
        muiCol.renderHeader = col.renderHeader;
      }

      // Width
      if (col.meta?.flex) {
        muiCol.flex = col.meta.flex;
        if (col.minSize) muiCol.minWidth = col.minSize;
      } else if (col.size) {
        muiCol.width = col.size;
      } else {
        muiCol.flex = 1;
      }

      // Cell renderer
      if (col.cell) {
        const CellFn = col.cell;
        muiCol.renderCell = (params) =>
          CellFn({
            getValue: () => params.value,
            row: { original: params.row, getIsSelected: () => false },
            column: col,
          });
      }

      return muiCol;
    });
  }, [columns]);

  // Sort model — convert our format to MUI format
  const sortModel = useMemo(() => {
    if (!sorting?.length) return [];
    return sorting.map((s) => ({
      field: s.id,
      sort: s.desc ? "desc" : "asc",
    }));
  }, [sorting]);

  const handleSortModelChange = useCallback(
    (newModel) => {
      if (!onSortingChange) return;
      if (newModel.length === 0) {
        onSortingChange([]);
      } else {
        const s = newModel[0];
        onSortingChange([{ id: s.field, desc: s.sort === "desc" }]);
      }
    },
    [onSortingChange],
  );

  // Row selection — convert between array (MUI) and object (our API)
  const selectionModel = useMemo(() => {
    if (!rowSelection) return [];
    return Object.keys(rowSelection)
      .filter((k) => rowSelection[k])
      .map((k) => {
        const row = (data || [])[parseInt(k, 10)];
        return row ? (getRowId ? getRowId(row) : row.id) : k;
      })
      .filter(Boolean);
  }, [rowSelection, data, getRowId]);

  const handleSelectionChange = useCallback(
    (newSelection) => {
      if (!onRowSelectionChange) return;
      const obj = {};
      (data || []).forEach((row, idx) => {
        const key = getRowId ? getRowId(row) : row.id;
        if (newSelection.includes(key)) obj[idx] = true;
      });
      onRowSelectionChange(obj);
    },
    [onRowSelectionChange, data, getRowId],
  );

  // Column visibility — convert our format to MUI format
  const muiColumnVisibility = useMemo(() => {
    if (!columnVisibility) return {};
    return columnVisibility;
  }, [columnVisibility]);

  return (
    <Box
      ref={containerRef}
      sx={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%" }}
    >
      <DataGrid
        rows={data || []}
        columns={muiColumns}
        loading={isLoading}
        getRowId={getRowId || ((row) => row.id)}
        rowCount={rowCount}
        rowHeight={rowHeight}
        columnHeaderHeight={headerHeight}
        // Server-side — we only pass the current page of data,
        // so tell DataGrid not to paginate internally
        paginationMode="server"
        sortingMode="server"
        filterMode="server"
        pagination={false}
        // Sort
        sortModel={sortModel}
        onSortModelChange={handleSortModelChange}
        // Selection
        checkboxSelection={enableSelection}
        rowSelectionModel={selectionModel}
        onRowSelectionModelChange={handleSelectionChange}
        disableRowSelectionOnClick
        // Column visibility
        columnVisibilityModel={muiColumnVisibility}
        onColumnVisibilityModelChange={onColumnVisibilityChange}
        // Row click
        onRowClick={onRowClick ? (params) => onRowClick(params.row) : undefined}
        getRowClassName={(params) =>
          activeRowId != null && params.id === activeRowId
            ? "DataTable-activeRow"
            : ""
        }
        // No virtualization — paginated at 50 rows max, plain DOM = no white flash
        disableVirtualization
        // Hide built-in pagination/footer — we use our own
        hideFooter
        // Disable built-in toolbar
        disableColumnFilter
        disableColumnSelector
        disableDensitySelector
        // Styling
        localeText={{ noRowsLabel: emptyMessage }}
        // Design system empty state instead of the grid's bare text, and
        // skeleton rows under the real headers on first load (the layout is
        // known, so rows copy the content) instead of the grid's spinner.
        slots={{
          noRowsOverlay: () => (
            <EmptyContent title={emptyMessage} sx={{ height: 1 }} />
          ),
          // A refetch over rows already shown only dims them, so paging,
          // search and refocus never wipe the table back to skeleton.
          loadingOverlay: () =>
            data?.length ? (
              <Box
                role="progressbar"
                aria-label="Loading"
                aria-busy="true"
                sx={{ height: 1, bgcolor: "background.paper", opacity: 0.5 }}
              />
            ) : (
              <SkeletonRowsOverlay rowHeight={rowHeight} />
            ),
        }}
        sx={{
          border: "none",
          bgcolor: "background.paper",
          // Header
          "& .MuiDataGrid-columnHeaders": {
            bgcolor: "background.paper",
            borderBottom: "1px solid",
            borderColor: "divider",
            minHeight: `${headerHeight}px !important`,
            maxHeight: `${headerHeight}px !important`,
          },
          "--DataGrid-containerBackground": theme.palette.background.paper,
          // Design system table: headers 11/500 in fg.tertiary, values 12/18.
          "& .MuiDataGrid-columnHeaderTitle": {
            fontSize: "11px",
            lineHeight: "16px",
            fontWeight: 500,
            color: theme.palette.text.subtitle,
          },
          "& .MuiDataGrid-iconButtonContainer": {
            color: theme.palette.text.secondary,
          },
          // Cells
          "& .MuiDataGrid-cell": {
            borderBottom: "1px solid",
            borderColor: "divider",
            fontSize: "12px",
            cursor: onRowClick ? "pointer" : "default",
            "&:focus, &:focus-within": { outline: "none" },
          },
          // Rows
          "& .MuiDataGrid-row:hover": {
            bgcolor: "action.hover",
          },
          "& .MuiDataGrid-row.DataTable-activeRow, & .MuiDataGrid-row.DataTable-activeRow:hover":
            { bgcolor: "action.selected" },
          // Clean up
          "& .MuiDataGrid-columnSeparator": { display: "none" },
          "& .MuiDataGrid-columnHeader:focus, & .MuiDataGrid-columnHeader:focus-within":
            { outline: "none" },
          "& .MuiDataGrid-overlayWrapper": { minHeight: 200 },
        }}
      />
    </Box>
  );
}
