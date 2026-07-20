import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, forwardRef, useRef, useEffect } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { ProcessingStatusCell, TitleCell } from "./knowledge-cells";
import PropTypes from "prop-types";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";
import { format } from "date-fns";
import { formatFileSize } from "src/utils/utils";
import { NUMBER_OF_ROWS_THAT_CAN_BE_SELECTED } from "./utils";
import logger from "src/utils/logger";
import { APP_CONSTANTS } from "src/utils/constants";

const cellCenterStyle = {
  display: "flex",
  alignItems: "center",
  height: "100%",
};

const SheetTableView = forwardRef((props, ref) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const {
    setSelectedFiles,
    setSelectedAll,
    selectedAll,
    debouncedSearchQuery,
    knowledgeId,
    setLastUpdatedDate,
    setStatus,
    setIsFetchingData,
    setTotalRows,
    setExcludingIds,
    totalRows,
    status,
  } = props;

  const isRefreshing = useRef(null);

  const refreshRowsManual = async () => {
    const totalPages = Object.keys(
      ref?.current?.api?.getCacheBlockState(),
    ).length;

    for (let p = 0; p < totalPages; p++) {
      try {
        const { data } = await axios.post(endpoints.knowledge.files, {
          kb_id: knowledgeId,
          page_number: p,
        });

        const rows = data?.result?.table_data ?? [];

        const transaction = {
          update: rows,
        };

        if (ref?.current?.api) {
          ref?.current?.api?.applyServerSideTransaction(transaction);
        }
        setStatus({
          status: data?.result?.status,
          status_count: data?.result?.status_count,
        });
      } catch (e) {
        logger.error("Failed to refresh rows", e);
      }
    }
  };

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Title",
        field: "name",
        flex: 1,
        cellRenderer: TitleCell,
        columnId: "name",
      },
      {
        headerName: "File size",
        field: "file_size",
        flex: 1,
        columnId: "file_size",
        cellStyle: cellCenterStyle,
        valueFormatter: (params) =>
          params?.value ? formatFileSize(params?.value) : "",
      },
      {
        headerName: "Processing status",
        field: "status",
        columnId: "status",
        flex: 1,
        cellStyle: cellCenterStyle,
        cellRenderer: ProcessingStatusCell,
      },
      {
        headerName: "Updated",
        field: "updated",
        flex: 1,
        columnId: "updated_at",
        cellStyle: cellCenterStyle,
        valueFormatter: (params) =>
          params.value
            ? format(new Date(params.value), "dd/MM/yyyy, h:mm aaa")
            : "",
      },
      {
        headerName: "Updated by ",
        field: "updated_by",
        cellStyle: cellCenterStyle,
        flex: 1,
        columnId: "updated_by",
      },
    ],
    [],
  );

  const defaultColDef = useMemo(
    () => ({
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      lockVisible: true,
    }),
    [],
  );

  useEffect(() => {
    if (ref?.current?.api) {
      ref?.current?.api?.resetColumnState();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knowledgeId]);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        setIsFetchingData(true);
        const { request } = params;

        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        const sort = request?.sortModel?.map(({ colId, sort }) => {
          const columnId = columnDefs.find(
            (columnDef) => columnDef.field === colId,
          )?.columnId;
          return {
            column_id: columnId,
            type: sort === "asc" ? "ascending" : "descending",
          };
        });

        try {
          const { data } = await axios.post(endpoints.knowledge.files, {
            search: debouncedSearchQuery ?? "",
            kb_id: knowledgeId,
            page_number: pageNumber,
            page_size: pageSize,
            sort: sort,
          });

          const lastUpdated = data?.result?.last_updated;
          if (lastUpdated) {
            setLastUpdatedDate(lastUpdated);
          }

          const totalRows = data?.result?.total_rows ?? 0;
          setTotalRows(totalRows);

          if (data?.result?.status) {
            setStatus({
              status: data?.result?.status,
              status_count: data?.result?.status_count,
            });
          }
          const rows = data?.result?.table_data ?? [];

          params.success({
            rowData: rows,
            rowCount: totalRows,
          });

          const displayedNodes = [];
          params.api.forEachNode((node) => {
            if (node.displayed) {
              displayedNodes.push(node);
            }
          });

          if (displayedNodes?.length === 0) {
            setTimeout(() => {
              params?.api?.showNoRowsOverlay();
            }, 100);
          } else {
            setTimeout(() => {
              params?.api?.hideOverlay();
            }, 100);
          }
        } catch (error) {
          setTotalRows(0);
          params.fail();
          isRefreshing.current = null;
          setTimeout(() => {
            params?.api?.showNoRowsOverlay();
          }, 100);
        } finally {
          setIsFetchingData(false);
        }
        return [];
      },
      getRowId: (data) => data.id,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debouncedSearchQuery, knowledgeId],
  );

  const onSelectionChanged = (event) => {
    if (event?.source === "apiSelectAll") {
      setSelectedFiles([]);
      return;
    }

    if (!event) {
      setTimeout(() => {
        setSelectedAll(false);
        setExcludingIds(new Set());
        setSelectedFiles([]);
        ref?.current?.api?.deselectAll();
      }, 300);
      return;
    }

    const rowId = event.data?.id;

    if (selectedAll) {
      setSelectedFiles([]);
      setExcludingIds((prev) => {
        const next = new Set(prev);
        if (!event?.node?.isSelected()) {
          next.add(rowId);
        } else {
          next.delete(rowId);
        }
        return next;
      });
    } else {
      setExcludingIds(new Set());
      setSelectedFiles((prevSelectedItems) => {
        const updatedSelectedRowsData = [...prevSelectedItems];

        const rowIndex = updatedSelectedRowsData.findIndex(
          (row) => row?.id === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event.data);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }

        return updatedSelectedRowsData;
      });
    }
  };

  useEffect(() => {
    // refresh rows if status is not completed
    let interval;
    if (status !== "Completed") {
      interval = setInterval(() => {
        refreshRowsManual();
      }, 10000);
    } else {
      // refresh rows manually once, after status is completed
      refreshRowsManual();
    }
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  return (
    <Box
      component={"div"}
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        height: 600,
        width: "100%",
      }}
    >
      <AgGridReact
        ref={ref}
        rowHeight={54}
        defaultColDef={defaultColDef}
        theme={agTheme}
        columnDefs={columnDefs}
        rowSelection={{ mode: "multiRow" }}
        rowModelType="serverSide"
        serverSideStoreType="partial"
        suppressServerSideFullWidthLoadingRow={true}
        pagination={false}
        cacheBlockSize={10}
        // maxBlocksInCache={10}
        serverSideInitialRowCount={10}
        serverSideDatasource={dataSource}
        suppressContextMenu={true}
        getRowId={({ data }) => data.id}
        onHeaderCellClicked={(event) => {
          if (event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
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
          if (event?.column?.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
            return;

          if (totalRows <= NUMBER_OF_ROWS_THAT_CAN_BE_SELECTED) {
            const displayedNodes = [];
            event.api.forEachNode((node) => {
              if (node.displayed) {
                displayedNodes.push(node);
              }
            });

            const allSelected = displayedNodes.every((node) =>
              node.isSelected(),
            );

            if (!allSelected) {
              event.api.deselectAll();
              setSelectedFiles([]);
            } else {
              event.api.selectAll();
              const selectedNodes = [];
              event.api.forEachNode((node) => {
                if (node.isSelected()) {
                  selectedNodes.push(node.data);
                }
              });
              setSelectedFiles(selectedNodes);
            }
          } else {
            if (selectedAll) {
              setSelectedAll(false);
              event.api.deselectAll();
              setExcludingIds(new Set());
              setSelectedFiles([]);
            } else {
              setSelectedAll(true);
              event.api.selectAll();
              setExcludingIds(new Set());
              setSelectedFiles([]);
            }
          }
        }}
        onRowSelected={onSelectionChanged}
        onCellClicked={(event) => {
          if (
            event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
          ) {
            const selected = event.node.isSelected();
            event.node.setSelected(!selected);
          }
        }}
        isApplyServerSideTransaction={() => true}
        suppressRowTransform={true}
        suppressAnimationFrame={true}
      />
    </Box>
  );
});

SheetTableView.displayName = "SheetTableView";

SheetTableView.propTypes = {
  setSelectedFiles: PropTypes.func,
  setStatus: PropTypes.func,
  setLastUpdatedDate: PropTypes.func,
  setSelectedAll: PropTypes.func,
  selectedAll: PropTypes.bool,
  debouncedSearchQuery: PropTypes.string,
  knowledgeId: PropTypes.string,
  setIsFetchingData: PropTypes.func,
  ref: PropTypes.any,
  setTotalRows: PropTypes.func,
  setExcludingIds: PropTypes.func,
  totalRows: PropTypes.number,
  status: PropTypes.string,
};

export default SheetTableView;
