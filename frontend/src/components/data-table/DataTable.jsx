/* eslint-disable react/prop-types */
import { useCallback, useMemo } from "react";
import { DataGrid } from "@mui/x-data-grid";
import { Box, useTheme } from "@mui/material";

/**
 * DataTable — thin wrapper on MUI X DataGrid (already in the project).
 * Server-side pagination, sorting, selection. No virtualization white flash.
 */
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
  rowHeight = 44,
  headerHeight = 40,
  enableSelection = false,
}) {
  const theme = useTheme();

  // Convert our column format → MUI DataGrid columns
  const muiColumns = useMemo(() => {
    return columns.map((col) => {
      const muiCol = {
        field: col.id || col.accessorKey,
        headerName: col.header,
        sortable: col.enableSorting !== false,
        disableColumnMenu: true,
        resizable: true,
      };

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
    <Box sx={{ flex: 1, minWidth: 0, minHeight: 0 }}>
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
          "& .MuiDataGrid-columnHeaderTitle": {
            fontSize: "13px",
            fontWeight: 500,
            color: theme.palette.text.secondary,
          },
          "& .MuiDataGrid-iconButtonContainer": {
            color: theme.palette.text.secondary,
          },
          // Cells
          "& .MuiDataGrid-cell": {
            borderBottom: "1px solid",
            borderColor: "divider",
            fontSize: "13px",
            cursor: onRowClick ? "pointer" : "default",
            "&:focus, &:focus-within": { outline: "none" },
          },
          // Rows
          "& .MuiDataGrid-row:hover": {
            bgcolor:
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.03)"
                : "rgba(0,0,0,0.02)",
          },
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
