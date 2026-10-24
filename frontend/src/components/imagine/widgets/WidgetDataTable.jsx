/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgTheme } from "src/hooks/use-ag-theme";

export default function WidgetDataTable({ config }) {
  const agTheme = useAgTheme();

  const columns = useMemo(
    () =>
      (config.columns || []).map((col) => {
        if (typeof col === "string") {
          return { field: col, headerName: col, flex: 1, minWidth: 100 };
        }
        return {
          field: col.field,
          headerName: col.headerName || col.field,
          flex: col.flex ?? 1,
          minWidth: col.minWidth ?? 100,
          maxWidth: col.maxWidth,
          sortable: col.sortable ?? true,
        };
      }),
    [config.columns],
  );

  const rows = config.rows || [];

  const defaultColDef = useMemo(
    () => ({
      resizable: true,
      sortable: true,
      suppressMovable: true,
    }),
    [],
  );

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        height: "100%",
        width: "100%",
        "& .ag-header": { fontSize: 12, fontWeight: 600 },
        "& .ag-cell": { fontSize: 12 },
        "& .ag-root-wrapper": { border: "none" },
      }}
    >
      <AgGridReact
        theme={agTheme}
        columnDefs={columns}
        rowData={rows}
        defaultColDef={defaultColDef}
        domLayout="normal"
        headerHeight={32}
        rowHeight={30}
        suppressCellFocus
        animateRows={false}
      />
    </Box>
  );
}

WidgetDataTable.propTypes = { config: PropTypes.object.isRequired };
