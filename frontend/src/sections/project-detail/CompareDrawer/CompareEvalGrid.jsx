import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import StatusCellRenderer from "src/sections/project-detail/CompareDrawer/StatusCellRenderer";

const CompareEvalGrid = ({ rowData }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);

  const TabColumnDefs = [
    {
      headerName: "Evalutation Metrics",
      field: "name",
      flex: 1,
    },
    {
      headerName: "Score",
      field: "score",
      flex: 1,
      cellRenderer: StatusCellRenderer,
    },
  ];

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: true,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  return (
    <Box
      className="ag-theme-alpine"
      sx={{
        height: "100%",
        width: "100%",
        overflow: "hidden",
        "& ::-webkit-scrollbar": {
          width: "5px",
          height: "6px",
        },
        "& ::-webkit-scrollbar-thumb": {
          backgroundColor: "border.strong",
          borderRadius: "4px",
        },
        "& ::-webkit-scrollbar-track": {
          background: "transparent",
        },
      }}
    >
      <AgGridReact
        theme={agTheme}
        columnDefs={TabColumnDefs}
        defaultColDef={defaultColDef}
        rowData={rowData}
        domLayout={"normal"}
        suppressRowDrag={true}
        noRowsOverlayComponent={() => NoRowsOverlay("No evaluations applied")}
      />
    </Box>
  );
};

CompareEvalGrid.propTypes = {
  rowData: PropTypes.array,
};

export default CompareEvalGrid;
