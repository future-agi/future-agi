import React, { forwardRef, useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { Box } from "@mui/material";
import PropTypes from "prop-types";

const GRID_TABLE_THEME_PARAMS = { headerHeight: "39px" };

const GridTable = forwardRef(
  (
    { onGridReady, columnDefs, defaultCols = {}, otherGridOption = {} },
    ref,
  ) => {
    const agTheme = useAgThemeWith(GRID_TABLE_THEME_PARAMS);
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        sortable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellStyle: {
          paddingX: 0.5,
          paddingY: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
        ...defaultCols,
      }),
      [defaultCols],
    );

    return (
      <Box height={"100%"}>
        <AgGridReact
          rowHeight={36}
          ref={ref}
          theme={agTheme}
          defaultColDef={defaultColDef}
          pagination={false}
          cacheBlockSize={20}
          maxBlocksInCache={10}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={10}
          suppressContextMenu={true}
          rowModelType="serverSide"
          getRowId={({ data }) =>
            data.id || data.rowId || data.user_id || data.userId
          }
          onGridReady={onGridReady}
          columnDefs={columnDefs}
          {...otherGridOption}
        />
      </Box>
    );
  },
);

GridTable.displayName = "GridTable";

export default GridTable;

GridTable.propTypes = {
  onGridReady: PropTypes.func,
  columnDefs: PropTypes.any,
  defaultCols: PropTypes.object,
  otherGridOption: PropTypes.object,
};
