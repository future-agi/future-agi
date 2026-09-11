import { Box, useTheme } from "@mui/material";
import React, { forwardRef, useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { dateValueFormatter } from "src/utils/dateTimeUtils";
import logger from "src/utils/logger";
import { APP_CONSTANTS } from "src/utils/constants";

const MonitorGrid = forwardRef(
  (
    {
      onSelectionChanged,
      searchQuery,
      setSelectedRowsData,
      selectedAll,
      setSelectedAll,
      setIsDataEmpty,
      setIsEditingAlertId,
    },
    ref,
  ) => {
    const { observeId } = useParams();
    const theme = useTheme();
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);

    const columnDefs = useMemo(
      () => [
        {
          headerName: "Alert title",
          field: "name",
          flex: 1,
          sortable: true,
        },
        {
          headerName: "Metrics",
          field: "metric",
          flex: 1,
        },
        {
          headerName: "Updated",
          field: "updated_at",
          flex: 1,
          sortable: true,
          valueFormatter: dateValueFormatter,
        },
      ],
      [],
    );

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          const { request } = params;
          // request has startRow and endRow get next page number and each page has 10 rows
          const pageSize = request.endRow - request.startRow;
          const pageNumber = Math.floor(request.startRow / pageSize);

          const sortModel = request?.sortModel?.[0] || {};
          const { colId: sort_by, sort: sort_direction } = sortModel;

          try {
            setSelectedRowsData([]);
            onSelectionChanged(null);

            const { data } = await axios.get(
              endpoints.project.getMonitorList(),
              {
                params: {
                  page_number: pageNumber,
                  page_size: pageSize,
                  search_text: searchQuery,
                  project_id: observeId,
                  sort_by,
                  sort_direction,
                },
              },
            );

            const rows = data?.result?.table;
            // const cols = data?.result?.columnConfig;

            // Always set isDataEmpty based on data length, regardless of search query
            // This ensures proper state transitions when changing projects
            setIsDataEmpty(
              !searchQuery && rows.length === 0 && pageNumber === 0,
            );

            params.success({
              rowData: rows,
              rowCount: data?.result?.metadata?.total_rows,
            });
          } catch (error) {
            params.fail();
            logger.error("Failed to get monitor list", error);
          }
        },
        getRowId: (data) => data.id,
      }),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [searchQuery, observeId],
    );

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        sortable: false,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
      }),
      [],
    );

    const gridOptions = {
      pagination: true,
      rowSelection: { mode: "multiRow", enableClickSelection: false },
      paginationAutoPageSize: true,
    };

    return (
      <Box sx={{ flex: 1 }}>
        <Box
          sx={{
            flex: 1,
            paddingTop: theme.spacing(1.5),
            paddingBottom: theme.spacing(2),
            height: "100%",
          }}
        >
          <Box
            className="ag-theme-quartz"
            style={{ height: "calc(100vh - 215px)" }}
          >
            <AgGridReact
              ref={ref}
              // ref={(params) => {
              //   gridRef.current = params;
              //   ref.current = params;
              // }}
              theme={agTheme}
              serverSideDatasource={dataSource}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              pagination={gridOptions.pagination}
              paginationAutoPageSize={gridOptions.paginationAutoPageSize}
              rowSelection={gridOptions.rowSelection}
              suppressMultiSort={true}
              paginationPageSizeSelector={false}
              suppressServerSideFullWidthLoadingRow={true}
              serverSideInitialRowCount={10}
              rowModelType="serverSide"
              maxBlocksInCache={1}
              cacheBlockSize={10}
              onRowSelected={onSelectionChanged}
              onHeaderCellClicked={(event) => {
                if (
                  event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                ) {
                  const displayedNodes = [];
                  event.api.forEachNode((node) => {
                    if (node.displayed) {
                      displayedNodes.push(node);
                    }
                  });
                  const allSelected = displayedNodes.every((node) =>
                    node.isSelected(),
                  );

                  if (allSelected) {
                    event.api.deselectAll();
                  } else {
                    event?.api.selectAll();
                  }
                }
              }}
              onColumnHeaderClicked={(event) => {
                if (
                  event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                ) {
                  return;
                }

                if (selectedAll) {
                  event.api.deselectAll();
                  setSelectedAll(false);
                } else {
                  event.api.selectAll();
                  setSelectedAll(true);
                }
              }}
              onCellClicked={(params) => {
                if (
                  params?.column?.getColId() ===
                  APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                ) {
                  const selected = params.node.isSelected();
                  params.node.setSelected(!selected);
                  return;
                }
                const alertId = params?.data?.id;
                if (alertId) {
                  setIsEditingAlertId(alertId);
                }
              }}
            />
          </Box>
        </Box>
      </Box>
    );
  },
);

MonitorGrid.displayName = "MonitorGrid";

MonitorGrid.propTypes = {
  onSelectionChanged: PropTypes.func,
  searchQuery: PropTypes.string,
  setSelectedRowsData: PropTypes.func,
  selectedAll: PropTypes.bool,
  setSelectedAll: PropTypes.func,
  setIsDataEmpty: PropTypes.func,
  setIsEditingAlertId: PropTypes.func,
};

export default MonitorGrid;
