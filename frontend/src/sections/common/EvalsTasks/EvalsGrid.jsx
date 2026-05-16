import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  forwardRef,
  useMemo,
  useRef,
  useState,
  useEffect,
  useCallback,
} from "react";
import "./evalTaskAg.css";
import CustomTooltip from "./Renderers/EvalsAndTasksCustomToolTip";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { getEvalsTaskColumnConfig } from "./common";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { preventHeaderSelection } from "src/utils/utils";
import logger from "src/utils/logger";
import { APP_CONSTANTS } from "src/utils/constants";

const EVALS_GRID_THEME_PARAMS = {
  headerColumnBorder: {
    width: "0px",
    rowVerticalPaddingScale: 2.6,
  },
};

// Named component for React Fast Refresh
const EvalsGrid = forwardRef(
  (
    {
      columns,
      setColumns,
      filters,
      observeId = null,
      setSelectedRowsData,
      setOpenDrawer,
      setIsView,
      debouncedSearchQuery,
      hasActiveFilter,
      setHasData,
      setIsLoading,
      setSearchState,
    },
    ref,
  ) => {
    const agTheme = useAgThemeWith(EVALS_GRID_THEME_PARAMS);
    const gridRef = useRef(null);
    const [selectedAll, setSelectedAll] = useState(false);
    const columnsInitializedRef = useRef(false);

    // Call once outside of render
    useEffect(() => {
      preventHeaderSelection();
    }, []);

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        flex: 1,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellStyle: {
          padding: "0px 20px",
          fontSize: "14px",
          height: "100%",
        },
      }),
      [],
    );

    // Get base column definitions
    const baseColumnDefs = useMemo(
      () => getEvalsTaskColumnConfig(observeId),
      [observeId],
    );

    // Apply column visibility settings and order from columns state
    const columnDefs = useMemo(() => {
      // If columns is empty or not set yet, return base column definitions
      if (!columns || columns.length === 0) {
        return baseColumnDefs;
      }

      // Create a map for faster lookups
      const columnMap = new Map();
      columns.forEach((col) => columnMap.set(col.id, col));

      // First apply visibility to existing columns
      const visibilityApplied = baseColumnDefs.map((colDef) => {
        const matchingColumn = columnMap.get(colDef.field);
        if (matchingColumn) {
          return {
            ...colDef,
            hide: !matchingColumn.isVisible,
          };
        }
        return colDef;
      });

      // Then reorder columns based on the order in 'columns' array
      // Create a map to track the order of columns
      const columnOrderMap = new Map();
      columns.forEach((col, index) => {
        columnOrderMap.set(col.id, index);
      });

      // Sort the columns based on their order in the 'columns' array
      return [...visibilityApplied].sort((a, b) => {
        const orderA = columnOrderMap.get(a.field) ?? Number.MAX_SAFE_INTEGER;
        const orderB = columnOrderMap.get(b.field) ?? Number.MAX_SAFE_INTEGER;
        return orderA - orderB;
      });
    }, [baseColumnDefs, columns]);

    // Update column state when grid is initialized - memoized to prevent unnecessary executions
    const initializeColumns = useCallback(() => {
      if (gridRef.current?.api && (columns === null || columns.length === 0)) {
        const gridColumns = gridRef.current.columnApi.getColumns();
        if (gridColumns) {
          const transformedColumns = gridColumns.map((col) => ({
            id: col.getColId(),
            name: col.getColDef().headerName || "",
            isVisible: !col.getColDef().hide,
            groupBy: null,
            outputType: null,
          }));
          setColumns(transformedColumns);
        }
      }
    }, [setColumns, columns]);

    useEffect(() => {
      if (gridRef.current?.api) {
        initializeColumns();
      }
    }, [gridRef.current?.api, initializeColumns]);

    // for polling
    const refreshRowsManual = async () => {
      const totalPages = Object.keys(
        gridRef?.current?.api?.getCacheBlockState(),
      ).length;

      const endpoint = observeId
        ? endpoints.project.getEvalTaskList()
        : endpoints.project.listEvalsWithProject();

      for (let p = 0; p < totalPages; p++) {
        try {
          const { data } = await axios.get(endpoint, {
            params: {
              name: debouncedSearchQuery?.length ? debouncedSearchQuery : null,
              page_number: p,
              ...(observeId && { project_id: observeId }),
              sort_params: JSON.stringify(
                gridRef.current?.api
                  ?.getColumnState()
                  ?.filter((c) => c?.sort != null)
                  ?.map(({ colId, sort }) => ({
                    column_id: colId,
                    direction: sort,
                  })),
              ),
              filters: JSON.stringify(filters),
            },
          });

          const rows = data?.result?.table ?? [];

          const transaction = {
            update: rows,
          };

          if (gridRef?.current?.api) {
            gridRef?.current?.api?.applyServerSideTransaction(transaction);
          }
        } catch (e) {
          logger.error("Failed to refresh eval tasks rows", e);
        }
      }
    };

    // Memoize data source creation to prevent unnecessary recreations
    const dataSource = useMemo(() => {
      const getRows = async (params) => {
        setIsLoading(true);
        const { request } = params;
        setSelectedAll(false);

        // Calculate pagination parameters
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        const endpoint = observeId
          ? endpoints.project.getEvalTaskList()
          : endpoints.project.listEvalsWithProject();

        try {
          const { data } = await axios.get(endpoint, {
            params: {
              name: debouncedSearchQuery?.length ? debouncedSearchQuery : null,
              page_number: pageNumber,
              page_size: pageSize,
              ...(observeId && { project_id: observeId }),
              sort_params: request?.sortModel?.map(({ colId, sort }) => ({
                column_id: colId,
                direction: sort,
              })),
              filters: JSON.stringify(filters),
            },
          });

          const rows = data?.result?.table;
          const hasResults = rows.length > 0;
          setHasData(hasResults);

          if (debouncedSearchQuery === "") {
            if (hasActiveFilter) {
              setSearchState("searching");
            } else {
              setSearchState(hasResults ? "idle" : "empty");
            }
          } else {
            setSearchState("searching");
          }

          // Only initialize columns once on first data load
          if (!columnsInitializedRef.current) {
            columnsInitializedRef.current = true;
            const transformedColumns = baseColumnDefs.map((col) => ({
              id: col.field || null,
              name: col.headerName || "",
              isVisible: true,
              groupBy: null,
              outputType: null,
            }));

            setColumns(transformedColumns);
          }

          params.success({
            rowData: rows,
            rowCount: data?.result?.metadata?.total_rows,
          });
        } catch (error) {
          params.fail();
          setHasData(false);
          if (debouncedSearchQuery === "") {
            setSearchState("empty");
          }
        } finally {
          setIsLoading(false);
        }
      };
      return {
        getRows,
        getRowId: (data) => data.id,
      };
    }, [
      filters,
      observeId,
      debouncedSearchQuery,
      baseColumnDefs,
      setHasData,
      setIsLoading,
      setSearchState,
    ]);

    // Memoize selection handler to prevent recreation on each render
    const onSelectionChanged = useCallback(
      (event) => {
        if (!event) {
          setTimeout(() => {
            setSelectedRowsData([]);
          }, 300);
          gridRef?.current?.api?.deselectAll();
          return;
        }
        const rowId = event.data.id;

        setSelectedRowsData((prevSelectedRowsData) => {
          // Use functional update to avoid stale closures
          const rowIndex = prevSelectedRowsData.findIndex(
            (row) => row.id === rowId,
          );

          if (rowIndex === -1) {
            return [...prevSelectedRowsData, event.data];
          } else {
            const newData = [...prevSelectedRowsData];
            newData.splice(rowIndex, 1);
            return newData;
          }
        });
      },
      [setSelectedRowsData],
    );

    // Optimized with useCallback to prevent recreation on each render
    const applyColumnChanges = useCallback(() => {
      if (
        !gridRef.current?.api ||
        !gridRef.current?.columnApi ||
        !columns ||
        columns.length === 0
      ) {
        return;
      }

      // Update column visibility and order
      const currentOrder = gridRef.current.columnApi
        .getAllGridColumns()
        .map((col) => col.getColId());
      const targetOrder = columns.map((col) => col.id);

      // Only reorder if the order is different
      if (JSON.stringify(currentOrder) !== JSON.stringify(targetOrder)) {
        gridRef.current.columnApi.setColumnState(
          columns.map((col) => ({
            colId: col.id,
            hide: !col.isVisible,
            sort: null,
            sortIndex: null,
          })),
        );
      } else {
        // Just update visibility - more efficient
        columns.forEach((col) => {
          gridRef.current.columnApi.setColumnsVisible([col.id], col.isVisible);
        });
      }

      // Refresh cells to ensure everything renders correctly
      gridRef.current.api.refreshCells({ force: true });
    }, [columns]);

    // Apply column changes when columns state changes
    useEffect(() => {
      if (columns && columns.length > 0 && gridRef.current?.api) {
        applyColumnChanges();
      }
    }, [columns, applyColumnChanges]);

    // Memoize event handlers to prevent recreations
    const onColumnHeaderClicked = useCallback(
      (event) => {
        if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
          return;

        if (selectedAll) {
          event.api.deselectAll();
          setSelectedAll(false);
        } else {
          event.api.selectAll();
          setSelectedAll(true);
        }
      },
      [selectedAll],
    );

    const onGridReady = useCallback(() => {
      // Initial application of column settings
      if (columns && columns.length > 0) {
        applyColumnChanges();
      }
    }, [columns, applyColumnChanges]);

    const onCellClicked = useCallback(
      (event) => {
        if (event?.column?.colId === "status") {
          return;
        }
        if (
          event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
        ) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          return;
        }

        setOpenDrawer(event.data);
        setIsView(true);
      },
      [setOpenDrawer, setIsView],
    );

    const getRowHeight = useCallback((params) => {
      return params.node.rowPinned === "bottom" ? 30 : 48;
    }, []);

    // Memoize the components for better performance
    const frameworkComponents = useMemo(
      () => ({
        customTooltip: CustomTooltip,
      }),
      [],
    );

    useEffect(() => {
      const interval = setInterval(() => {
        refreshRowsManual();
      }, 10000);
      return () => clearInterval(interval);
    }, [debouncedSearchQuery, filters]);

    return (
      <Box
        className="ag-theme-alpine"
        style={{
          flex: 1,
          paddingRight: "12px",
        }}
        sx={{
          height: "100%",
          marginTop: 2,
          position: "relative",
        }}
      >
        <Box
          className="ag-theme-alpine eval-task-ag-grid"
          style={{ height: "100%", overflowX: "auto" }}
        >
          <AgGridReact
            ref={(params) => {
              gridRef.current = params;
              if (ref) ref.current = params;
            }}
            columnDefs={columnDefs}
            getRowHeight={getRowHeight}
            rowSelection={{ mode: "multiRow" }}
            pagination={true}
            theme={agTheme}
            onColumnHeaderClicked={onColumnHeaderClicked}
            serverSideDatasource={dataSource}
            paginationPageSize={10}
            cacheBlockSize={10}
            defaultColDef={defaultColDef}
            suppressRowClickSelection={true}
            rowStyle={{ cursor: "pointer" }}
            tooltipShowDelay={500}
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={5}
            tooltipHideDelay={1600}
            tooltipInteraction={true}
            frameworkComponents={frameworkComponents}
            paginationAutoPageSize={true}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            maxBlocksInCache={1}
            getRowId={(data) => data.data.id}
            onRowSelected={onSelectionChanged}
            onGridReady={onGridReady}
            onCellClicked={onCellClicked}
          />
        </Box>
      </Box>
    );
  },
);

EvalsGrid.displayName = "EvalsGrid";

EvalsGrid.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  filters: PropTypes.array,
  observeId: PropTypes.string,
  setSelectedRowsData: PropTypes.func,
  setOpenDrawer: PropTypes.func,
  setIsView: PropTypes.func,
  debouncedSearchQuery: PropTypes.string,
  setHasData: PropTypes.func,
  setIsLoading: PropTypes.func,
  setSearchState: PropTypes.func,
  hasActiveFilter: PropTypes.bool,
};

// Apply memo separately before export to fix Fast Refresh warning
const MemoizedEvalsGrid = React.memo(EvalsGrid);

// Named export for Fast Refresh
export default MemoizedEvalsGrid;
