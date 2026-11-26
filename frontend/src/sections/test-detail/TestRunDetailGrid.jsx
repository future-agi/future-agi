import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/utils/ag-grid-setup";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  FINISHED_RUN_STATUSES,
  getColumnDefsSignature,
  getSelectedCallExecutionIdsFilter,
  getTestRunDetailColumnQuery,
  getTestRunDetailGridColumnDefs,
  readFinalRunKpis,
  replaceToolOutputIdsWithNames,
  TestRunLoadingStatus,
  TEST_RUN_DETAIL_DEFAULT_PLACEHOLDER_COLUMNS,
} from "./common";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import logger from "../../utils/logger";
import {
  useTestDetailSearchStoreShallow,
  useTestDetailSideDrawerStoreShallow,
  useTestDetailStore,
  useTestDetailStoreShallow,
  useTestExecutionStore,
} from "./states";
import TestDetailSideDrawer from "./TestDetailDrawer/TestDetailSideDrawer";
import { useDebounce } from "src/hooks/use-debounce";
import { useTestDetail } from "./context/TestDetailContext";
import SingleImageViewerProvider from "../develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import { getComplexFilterValidation } from "src/components/ComplexFilter/common";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import PropTypes from "prop-types";
import "./test-detail.css";
import { postProcessPopup } from "../develop-detail/DataTab/common";
import TestDetailConfigureEval from "./TestDetailConfigureEval";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { APP_CONSTANTS } from "src/utils/constants";

const rowSelection = { mode: "multiRow" };
const rowStyle = { cursor: "pointer" };
const getRowId = ({ data }) => data.id;
const GridNoRowsOverlay = () => NoRowsOverlay("No test runs found");

const SelectionHeader = (props) => {
  const onCheckboxClick = (e) => {
    e.stopPropagation(); // Stop event from reaching the header
    const api = props.api;
    const { selectAll } = api.getServerSideSelectionState() || {
      selectAll: false,
    };
    if (selectAll) {
      api.setServerSideSelectionState({ selectAll: false, toggledNodes: [] });
    } else {
      api.setServerSideSelectionState({ selectAll: true, toggledNodes: [] });
    }
  };

  return (
    <div className="">
      <div onClick={onCheckboxClick} className=""></div>
    </div>
  );
};

SelectionHeader.propTypes = {
  api: PropTypes.shape({
    getServerSideSelectionState: PropTypes.func.isRequired,
    setServerSideSelectionState: PropTypes.func.isRequired,
  }).isRequired,
};

const selectionColumnDef = {
  pinned: true,
  lockPinned: true,
  headerComponent: SelectionHeader,
  cellStyle: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
};

const TestRunDetailGrid = () => {
  const gridTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const gridRef = useRef(null);
  const queryClient = useQueryClient();
  const { executionId } = useParams();
  const { setGridApi } = useTestDetail();
  const isRefreshingRef = useRef(false);
  const timeoutRef = useRef(null);
  // The execution this mounted grid shows. A read made for another execution,
  // or after the grid unmounted, must not write rows into it.
  const currentExecutionRef = useRef(null);
  // This grid's claim on the run status in the store (`claimStatus`).
  const [statusReader] = useState(() => ({}));
  // The finish whose final KPIs this page already reads, so overlapping reads
  // that all see the run finished read them once.
  const settledRunRef = useRef(null);
  const visibleReasonColumnsRef = useRef(new Set());
  const [_, setRowIndex] = useUrlState("rowIndex");
  const { columnDef, setColumnDef } = useTestDetailStoreShallow((state) => ({
    columnDef: state.columnDef,
    setColumnDef: state.setColumnDef,
  }));
  // Grid Options

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      // flex: 1,
      cellStyle: {
        lineHeight: 1,
        padding: "8px",
        display: "flex",
        alignItems: "center",
      },
    }),
    [],
  );

  const { setTestDetailDrawerOpen, testDetailDrawerOpen } =
    useTestDetailSideDrawerStoreShallow((state) => ({
      setTestDetailDrawerOpen: state.setTestDetailDrawerOpen,
      testDetailDrawerOpen: state.testDetailDrawerOpen,
    }));

  // Active call id — used by getRowStyle to highlight the row whose
  // detail drawer is currently open. Mirrors LLMTracing TraceGrid pattern.
  const activeCallId =
    testDetailDrawerOpen?.id || testDetailDrawerOpen?.trace_id || null;
  const getRowStyle = useCallback(
    (params) => {
      const rowId = params.data?.id || params.data?.trace_id;
      if (rowId && activeCallId && rowId === activeCallId) {
        return {
          backgroundColor: "var(--surface-row-active)",
          cursor: "pointer",
        };
      }
      return rowStyle;
    },
    [activeCallId],
  );

  const search = useTestDetailSearchStoreShallow((state) => state.search);

  const debouncedSearchQuery = useDebounce(search.trim(), 300);
  const filters =
    useTestDetailSearchStoreShallow((state) => state.filters) || [];
  const validatedFilters = useMemo(() => {
    return filters
      .map((filter) => {
        const result = getComplexFilterValidation(true, () => {}).safeParse(
          filter,
        );
        if (!result.success) {
          return false;
        }
        return result.data;
      })
      .filter(Boolean);
  }, [filters]);

  // Opening another execution starts over: no polling, no finish seen, and
  // no status or final count until this execution's own reads answer.
  useLayoutEffect(() => {
    currentExecutionRef.current = executionId;
    isRefreshingRef.current = false;
    settledRunRef.current = null;
    useTestExecutionStore.getState().claimStatus(executionId, statusReader);
    return () => {
      currentExecutionRef.current = null;
    };
  }, [executionId, statusReader]);

  const isCurrentGrid = useCallback(
    () => currentExecutionRef.current === executionId,
    [executionId],
  );

  // A read publishes the run status while its execution is on screen, also
  // after the user leaves Call Details. It stops once another execution opens,
  // the user leaves the run, or a newer grid mounts.
  const ownsRunStatus = useCallback(() => {
    const { statusExecutionId, statusReader: reader } =
      useTestExecutionStore.getState();
    return statusExecutionId === executionId && reader === statusReader;
  }, [executionId, statusReader]);

  // Publishes a status read for this execution. A running status starts
  // polling and drops the final count of an earlier finish. The first finished
  // status, on a return visit too, reads the final KPIs once. Polling stops
  // only when a poll reads every cached block finished (refreshRowsManual).
  const trackRunStatus = useCallback(
    (status) => {
      if (!ownsRunStatus()) return;
      const executionStore = useTestExecutionStore.getState();
      executionStore.setStatus(executionId, status);
      const runStatus = status?.toLowerCase();
      if (TestRunLoadingStatus.includes(runStatus)) {
        isRefreshingRef.current = true;
        settledRunRef.current = null;
        if (executionStore.kpisUpdateCountAtFinish !== null) {
          executionStore.setKpisUpdateCountAtFinish(executionId, null);
        }
      } else if (
        FINISHED_RUN_STATUSES.includes(runStatus) &&
        !settledRunRef.current
      ) {
        const settle = {};
        settledRunRef.current = settle;
        // The read lands in the KPI cache even if the user leaves the tab.
        readFinalRunKpis(queryClient, executionId).catch((error) => {
          logger.warn("Failed to read the final KPIs of the run", { error });
          if (settledRunRef.current === settle && ownsRunStatus()) {
            useTestExecutionStore.getState().markFinalKpisFailed(executionId);
          }
        });
      }
    },
    [executionId, queryClient, ownsRunStatus],
  );

  const dataSource = useMemo(
    () => {
      // Hide stale overlay immediately when datasource changes (search/filter change)
      gridRef?.current?.api?.hideOverlay();
      return {
        getRows: async (params) => {
          const { request } = params;
          const pageSize = request ? request?.endRow - request?.startRow : 10;
          const pageNumber = Math.floor((request?.startRow ?? 1) / pageSize);

          const callExecutionIdsFilters = getSelectedCallExecutionIdsFilter();

          const newFilters = [...validatedFilters];
          newFilters.push(...callExecutionIdsFilters);
          const query = getTestRunDetailColumnQuery(
            executionId,
            pageNumber,
            debouncedSearchQuery,
            newFilters,
            pageSize,
          );
          try {
            const { data } = await queryClient.fetchQuery(query);
            const status = data?.status;
            if (status) trackRunStatus(status);
            // AG Grid holds a load slot until a read succeeds or fails, so a
            // read for a superseded grid fails without showing its rows.
            if (!isCurrentGrid()) {
              params.fail();
              return;
            }
            const totalRows = data?.count ?? 0;
            params.api.setGridOption("context", {
              totalRowCount: totalRows ?? 0,
            });

            const currentColumnDef = useTestDetailStore.getState().columnDef;
            const newColDefs = getTestRunDetailGridColumnDefs(
              data?.column_order,
            );
            if (
              getColumnDefsSignature(currentColumnDef) !==
              getColumnDefsSignature(newColDefs)
            ) {
              setColumnDef(applyReasonColumnVisibility(newColDefs));
            }

            const updatedRows = replaceToolOutputIdsWithNames(data.results);

            // Only show "no rows" overlay when first page is empty
            if (
              (updatedRows?.length === 0 || totalRows === 0) &&
              (request?.startRow ?? 0) === 0
            ) {
              params.api.showNoRowsOverlay();
            } else {
              params.api.hideOverlay();
            }

            const rows =
              updatedRows?.map((row) => ({
                ...row,
                overall_status: status,
              })) || [];

            // Infinite scroll: only reveal total on last page
            const isLastPage = rows.length < pageSize;
            const lastRow = isLastPage
              ? (request?.startRow ?? 0) + rows.length
              : -1;

            params.success({
              rowData: rows,
              rowCount: lastRow,
            });

            // Prefetch next page so scroll feels instant
            if (!isLastPage) {
              const nextQuery = getTestRunDetailColumnQuery(
                executionId,
                pageNumber + 1,
                debouncedSearchQuery,
                newFilters,
              );
              queryClient.prefetchQuery(nextQuery);
            }
          } catch (error) {
            logger.warn("Failed to get test run detail", { error });
            params.fail();
            if (isCurrentGrid()) params.api.showNoRowsOverlay();
          }
        },
        getRowId: (data) => data.id,
      };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      executionId,
      queryClient,
      debouncedSearchQuery,
      validatedFilters,
      trackRunStatus,
      isCurrentGrid,
    ],
  );

  const applyReasonColumnVisibility = useCallback((colDefs) => {
    const visibleCols = visibleReasonColumnsRef.current;
    if (visibleCols.size === 0) return colDefs;
    return colDefs.map((colDef) => {
      if (colDef.children) {
        return {
          ...colDef,
          children: colDef.children.map((child) => {
            if (
              child.colId?.endsWith("_reason") &&
              visibleCols.has(child.colId)
            ) {
              return { ...child, hide: false };
            }
            return child;
          }),
        };
      }
      return colDef;
    });
  }, []);

  const refreshRowsManual = useCallback(async () => {
    if (!gridRef?.current?.api) return;
    // A run with no call rows has no cached block; read page 0 for its status.
    const totalPages = Math.max(
      Object.keys(gridRef?.current?.api?.getCacheBlockState()).length,
      1,
    );

    // const filters = useTestDetailStore.getState().filters || [];
    const search = useTestDetailStore.getState().search;

    const validatedFilters = filters
      .map((filter) => {
        const result = getComplexFilterValidation(true, () => {}).safeParse(
          filter,
        );
        if (!result.success) {
          return false;
        }
        return result.data;
      })
      .filter(Boolean);

    const callExecutionIdsFilters = getSelectedCallExecutionIdsFilter();

    validatedFilters.push(...callExecutionIdsFilters);

    let status;
    // Rows read while the run was still making calls keep that state until a
    // later poll reads them again.
    let everyBlockFinished = true;
    for (let pageNumber = 0; pageNumber < totalPages; pageNumber++) {
      try {
        const query = getTestRunDetailColumnQuery(
          executionId,
          pageNumber,
          search,
          validatedFilters,
        );

        const d = await query.queryFn();
        // A sweep goes on after the user leaves Call Details; the rows it
        // reads update nothing once the grid is gone.
        if (!ownsRunStatus()) return;
        const data = d.data;

        status = data?.status;
        if (!FINISHED_RUN_STATUSES.includes(status?.toLowerCase())) {
          everyBlockFinished = false;
        }
        const updatedRows = replaceToolOutputIdsWithNames(data.results);
        const rows = updatedRows?.map((row) => ({
          ...row,
          overall_status: status,
        }));

        const columns = data?.column_order || [];

        queryClient.setQueryData(query.queryKey, d);

        const currentColumnDef = useTestDetailStore.getState().columnDef;
        const newColDefs = getTestRunDetailGridColumnDefs(columns);
        if (
          getColumnDefsSignature(newColDefs) !==
          getColumnDefsSignature(currentColumnDef)
        ) {
          // If the columns have changed, update the query data no need to use transaction
          logger.debug("columns have changed, updating query data", {
            columns,
            currentColumnDef,
          });
          setColumnDef(applyReasonColumnVisibility(newColDefs));
        }
        const transaction = {
          update: rows,
        };

        const res =
          gridRef?.current?.api?.applyServerSideTransaction(transaction);
        logger.debug("res", res);
      } catch (e) {
        everyBlockFinished = false;
        logger.error("Error:", e);
      }
    }
    // No page read a status: keep the store and keep polling. A failed read
    // skips the check above, so check again.
    if (!status || !ownsRunStatus()) return;
    trackRunStatus(status);
    if (everyBlockFinished) isRefreshingRef.current = false;
  }, [
    executionId,
    queryClient,
    applyReasonColumnVisibility,
    trackRunStatus,
    ownsRunStatus,
  ]);

  const onRowSelectionChanged = useCallback(({ api, context }) => {
    const totalRowCount = context?.totalRowCount;
    const { selectAll, toggledNodes } = api.getServerSideSelectionState() || {
      selectAll: false,
      toggledNodes: [],
    };

    if (selectAll && totalRowCount - toggledNodes.length === 0) {
      api.deselectAll();
    }

    useTestDetailStore.setState({
      toggledNodes: toggledNodes,
      selectAll: selectAll,
    });
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      if (isRefreshingRef.current) {
        refreshRowsManual();
      }
    }, 5000);
    return () => {
      clearInterval(interval);
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [refreshRowsManual]);
  const drawerQueryKey = [
    "test-execution-detail-list",
    executionId,
    debouncedSearchQuery,
    validatedFilters || [],
  ];

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
    },
    [setGridApi],
  );

  const onCellClicked = useCallback(
    (params) => {
      if (
        params?.column?.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
      ) {
        const selected = params.node.isSelected();
        params.node.setSelected(!selected);
        return;
      }
      // Skip if click was on the "Listen" button (live call monitor)
      const target = params?.event?.target;
      if (target?.closest?.("[data-listen-btn]")) {
        return;
      }
      const rowData = params.data;
      setRowIndex({
        rowIndex: params.rowIndex,
        origin: "simulate",
        module: "simulate",
      });
      setTestDetailDrawerOpen({
        ...rowData,
        ignoreCache: true,
      });
    },
    [setRowIndex, setTestDetailDrawerOpen],
  );

  const onColumnVisible = useCallback((event) => {
    const colId = event.column?.getColId();
    if (colId?.endsWith("_reason")) {
      if (event.visible) {
        visibleReasonColumnsRef.current.add(colId);
      } else {
        visibleReasonColumnsRef.current.delete(colId);
      }
    }
  }, []);

  // Use default placeholder columns when columnDef is empty, otherwise use actual columnDef
  const finalColumnDefs = useMemo(() => {
    if (!columnDef || columnDef?.length === 0) {
      return TEST_RUN_DETAIL_DEFAULT_PLACEHOLDER_COLUMNS;
    }
    return columnDef;
  }, [columnDef]);

  return (
    <Box
      className="ag-theme-quartz"
      style={{
        height: "calc(100vh - 100px)",
        overflowY: "auto",
        backgroundColor: "var(--bg-paper)",
      }}
    >
      <AudioPlaybackProvider>
        <SingleImageViewerProvider>
          <AgGridReact
            ref={gridRef}
            theme={gridTheme}
            onGridReady={onGridReady}
            rowSelection={rowSelection}
            postProcessPopup={postProcessPopup}
            className="test-detail-grid"
            selectionColumnDef={selectionColumnDef}
            onSelectionChanged={onRowSelectionChanged}
            // 32px header, matching the design system table header.
            headerHeight={32}
            rowHeight={100}
            columnDefs={finalColumnDefs}
            defaultColDef={defaultColDef}
            suppressRowClickSelection={true}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={30}
            serverSideDatasource={dataSource}
            maxBlocksInCache={undefined}
            rowBuffer={10}
            cacheBlockSize={30}
            getRowStyle={getRowStyle}
            getRowId={getRowId}
            onCellClicked={onCellClicked}
            onColumnVisible={onColumnVisible}
            noRowsOverlayComponent={GridNoRowsOverlay}
          />
          <TestDetailSideDrawer
            drawerQueryKey={drawerQueryKey}
            isRefreshing={isRefreshingRef.current}
          />
        </SingleImageViewerProvider>
      </AudioPlaybackProvider>
      <TestDetailConfigureEval />
    </Box>
  );
};

export default TestRunDetailGrid;
