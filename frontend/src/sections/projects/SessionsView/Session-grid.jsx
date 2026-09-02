import { Box, LinearProgress, Skeleton, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import React, {
  useMemo,
  useRef,
  useState,
  useEffect,
  useCallback,
} from "react";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import TracesDrawer from "../TracesDrawer/TracesDrawer";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import {
  getSessionListColumnDef,
  initialVisibility,
  mergeNonCustomColumns,
} from "./common";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { userTraceRowHeightMapping } from "../UsersView/common";
import {
  normalizeConfigKeys,
  toBackendFilters,
} from "src/sections/projects/LLMTracing/common";
import {
  useSessionsGridStore,
  useSessionsGridStoreShallow,
} from "./ReplaySessions/store";
import { APP_CONSTANTS } from "src/utils/constants";
import {
  getListReadMessage,
  getListTotalState,
} from "src/sections/projects/LLMTracing/listTotalMetadata";
import {
  createListCursorPagination,
  isListCursorContinuationLimitError,
  loadExactListPage,
  retryServerSideCursorLoad,
  resumePendingListPage,
  shareInFlightListPage,
} from "src/sections/projects/LLMTracing/listCursorPagination";
import {
  boundObserveListRow,
  compactObserveListResponse,
} from "src/sections/projects/LLMTracing/observeListPayload";
import ListCursorContinuationNotice from "src/sections/projects/LLMTracing/ListCursorContinuationNotice";
import CursorGridPagination from "src/sections/projects/LLMTracing/CursorGridPagination";
import useCursorGridPagination from "src/sections/projects/LLMTracing/useCursorGridPagination";
import {
  dispatchObservePageChanged,
  OBSERVE_LIST_REFRESH_EVENT,
} from "../observeEvents";
import { isExpectedRequestCancellation } from "src/utils/cacheUtils";
import { isGridApiLive, withLiveGridApi } from "src/utils/gridApi";
import {
  OBSERVE_GRID_MAX_BLOCKS_IN_CACHE,
  OBSERVE_GRID_MAX_CONCURRENT_REQUESTS,
} from "src/config/runtime_limits";

const getSessionGridThemeParams = (theme) => ({
  columnBorder: false,
  rowVerticalPaddingScale: 2.6,
  headerColumnBorder: false,
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 0,
  rowBorder: { width: 1, color: "rgba(0,0,0,0.06)" },
  headerFontSize: "13px",
  headerFontWeight: theme.typography.fontWeightMedium,
  headerBackgroundColor: "transparent",
  headerTextColor: theme.palette.text.primary,
  rowHoverColor: "rgba(120,87,252,0.04)",
});

const sessionRowIdentity = (row) => {
  const id = row?.session_id || row?.id;
  return id ? `${row?.project_id || ""}:${id}` : null;
};

const LoadingHeader = () => {
  return <Skeleton variant="text" width={100} height={20} />;
};

const SessionGrid = React.forwardRef(
  (
    {
      updateObj,
      columns,
      setColumns,
      filters,
      projectId,
      cellHeight,
      onSelectionChanged,
      className,
      onGridReady,
      pendingCustomColumnsRef,
      canonicalOrderRef,
      isOnSavedView = false,
      onUserReorder,
      userIdForUserMode,
    },
    gridApiRef,
  ) => {
    const [open, setOpen] = useState(false);
    const [currentRowData, setCurrentRowData] = useState(null);
    const [continuationNotice, setContinuationNotice] = useState(null);
    const activeListReadsRef = useRef(0);
    const gridElementRef = useRef(null);
    const {
      beginPageLoad,
      page,
      pageCount,
      pageSize,
      changePageSize,
      finishPageLoad,
      goToPage,
      isPageLoading,
      publishPage,
      resetPagination,
    } = useCursorGridPagination(gridApiRef, gridElementRef);
    const continueCursorSearch = useCallback(() => {
      if (!continuationNotice) return;
      if (retryServerSideCursorLoad(gridApiRef?.current?.api)) {
        setContinuationNotice(null);
      }
    }, [continuationNotice, gridApiRef]);
    useEffect(() => {
      const refreshRows = () => {
        if (page > 1) {
          dispatchObservePageChanged(page);
          return;
        }
        if (activeListReadsRef.current > 0) return;
        withLiveGridApi(gridApiRef?.current?.api, (api) =>
          api.refreshServerSide?.({ purge: false }),
        );
      };
      window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshRows);
      return () =>
        window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshRows);
    }, [gridApiRef, page]);
    const theme = useTheme();
    const gridThemeParams = useMemo(
      () => getSessionGridThemeParams(theme),
      [theme],
    );
    const agTheme = useAgThemeWith(gridThemeParams);
    const handleDrawerClose = () => {
      setOpen(false);
    };

    const { toggledNodes, selectAll } = useSessionsGridStoreShallow((s) => ({
      totalRowCount: s.totalRowCount,
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
    }));

    // Track latest columns via ref to avoid recreating dataSource on visibility changes
    const columnsRef = useRef(columns);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);

    // Same trick for updateObj + isOnSavedView — dataSource closes over them
    // once when memoized, but getRows fires on every scroll/refetch and needs
    // the latest values. On a saved view we filter columns by `updateObj`
    // (the view's visibleColumns); on a default tab we fall through to
    // `res.config.isVisible` (the backend's per-project saved visibility).
    // Without this gate the data-fetch overwrites the saved view's columns
    // with the project default on every page load.
    const updateObjRef = useRef(updateObj);
    useEffect(() => {
      updateObjRef.current = updateObj;
    }, [updateObj]);

    const isOnSavedViewRef = useRef(isOnSavedView);
    useEffect(() => {
      isOnSavedViewRef.current = isOnSavedView;
    }, [isOnSavedView]);

    // Mirror columnDefs into a ref so the dataSource's getRows always reads
    // the latest. Without this, the dataSource memo (deps =
    // [filters, projectId, dateInterval]) captures columnDefs ONCE — when
    // columns is still []. That initial columnDefs uses the LoadingHeader
    // skeleton branch (line 107-117), and every subsequent getRows call
    // (page scroll, sort, etc.) writes those skeletons into filteredColumnDefs,
    // causing the header skeletons to flash back in randomly even after
    // proper headers had loaded.
    const columnDefsRef = useRef([]);

    const [dateInterval] = useUrlState("dateInterval", "day");

    // Grid Options
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressSizeToFit: false,
        cellStyle: {
          padding: "0px 20px",
          fontSize: "14px",
          height: "100%",
        },
      }),
      [],
    );

    const { columnDefs } = useMemo(() => {
      // Case 1: If no columns fetched yet → Return initial default columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: Object.keys(updateObj).map((title) => ({
            headerComponent: LoadingHeader,
            field: title,
            minWidth: 200,
            flex: 1,
          })),
          bottomRow: [],
        };
      }

      const grouping = {};
      const bottomRowObj = {};

      for (const eachCol of columns) {
        // Bucket each custom col alone so it stays flat in its store position
        // (a shared bucket collapsed them together and oscillated the order).
        if (eachCol?.groupBy && eachCol.groupBy !== "Custom Columns") {
          if (!grouping[eachCol?.groupBy]) {
            grouping[eachCol?.groupBy] = [eachCol];
          } else {
            grouping[eachCol?.groupBy].push(eachCol);
          }
        } else {
          grouping[getRandomId()] = [eachCol];
        }
      }

      const columnDefsResult = Object.entries(grouping).flatMap(
        ([group, cols]) => {
          if (group === "Annotation Metrics") {
            return cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
              return getSessionListColumnDef(c);
            });
          }
          if (cols.length === 1) {
            const c = cols[0];
            bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
            return getSessionListColumnDef(c);
          }
          // marryChildren + groupId keep the group movable across rebuilds.
          return {
            headerName: group,
            groupId: group,
            marryChildren: true,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              return getSessionListColumnDef(c);
            }),
          };
        },
      );

      return {
        columnDefs: columnDefsResult,
        bottomRow: [
          {
            ...bottomRowObj,
          },
        ],
      };
    }, [columns, updateObj]);

    useEffect(() => {
      columnDefsRef.current = columnDefs;
    }, [columnDefs]);

    const [filteredColumnDefs, setFilteredColumnDefs] = useState([]);

    const inFlightPageLoads = useRef(new Map());
    const cursorPagination = useRef(createListCursorPagination());
    const cursorQueryKeyRef = useRef(null);
    const paginationRequestKey = useMemo(
      () =>
        JSON.stringify({
          projectId: projectId || null,
          filters: toBackendFilters(filters),
          dateInterval: dateInterval || null,
          pageSize,
        }),
      [dateInterval, filters, pageSize, projectId],
    );
    const previousPaginationRequestKeyRef = useRef(paginationRequestKey);
    useEffect(() => {
      if (previousPaginationRequestKeyRef.current !== paginationRequestKey) {
        resetPagination();
      }
      previousPaginationRequestKeyRef.current = paginationRequestKey;
    }, [paginationRequestKey, resetPagination]);

    const dataSource = useMemo(
      () => {
        inFlightPageLoads.current.clear();
        cursorPagination.current.reset();
        cursorQueryKeyRef.current = null;
        return {
          getRows: async (params) => {
            let pageNumber = 0;
            let pageLoadRequestId = null;
            let pageLoadSucceeded = false;
            let pageLoadRowCount = 0;
            let requestGeneration = null;
            try {
              if (!isGridApiLive(params.api)) return;
              activeListReadsRef.current += 1;
              const { request } = params;

              const requestPageSize = request.endRow - request.startRow;
              pageNumber = Math.floor(request.startRow / requestPageSize);
              pageLoadRequestId = beginPageLoad(pageNumber);
              const sortParams = (request?.sortModel || []).map(
                ({ colId, sort }) => ({
                  column_id: colId,
                  direction: sort,
                }),
              );
              const backendFilters = toBackendFilters(filters);
              const queryKey = JSON.stringify({
                projectId: projectId || null,
                filters: backendFilters,
                dateInterval: dateInterval || null,
                sort: sortParams,
                pageSize: requestPageSize,
              });
              if (cursorQueryKeyRef.current !== queryKey) {
                inFlightPageLoads.current.clear();
                cursorPagination.current.reset();
                cursorQueryKeyRef.current = queryKey;
              }
              requestGeneration = cursorPagination.current.generation();

              const buildParams = (page) =>
                cursorPagination.current.requestParams(page, {
                  // Omit project_id when null — backend treats absent
                  // project_id as org-scoped (used by the cross-project
                  // user detail page).
                  ...(projectId ? { project_id: projectId } : {}),
                  page_size: requestPageSize,
                  sort_params: JSON.stringify(sortParams),
                  filters: JSON.stringify(backendFilters),
                  ...(dateInterval && { interval: dateInterval }),
                });

              const exactPage = await shareInFlightListPage({
                inFlight: inFlightPageLoads.current,
                key: `${requestGeneration}:${pageNumber}`,
                load: () =>
                  loadExactListPage({
                    pagination: cursorPagination.current,
                    pageNumber,
                    targetRowCount: requestPageSize,
                    loadResponse: (signal) =>
                      axios.get(endpoints.project.projectSessionList(), {
                        params: buildParams(pageNumber),
                        signal,
                      }),
                    rowsFromResponse: (response) =>
                      (response?.data?.result?.table || []).map(
                        boundObserveListRow,
                      ),
                    metadataFromResponse: (response) =>
                      response?.data?.result?.metadata || {},
                    compactResponse: compactObserveListResponse,
                    rowIdentity: sessionRowIdentity,
                    isCurrent: () =>
                      cursorPagination.current.isCurrent(requestGeneration),
                    nextResponse: (_cursor, signal) =>
                      axios.get(endpoints.project.projectSessionList(), {
                        params: buildParams(pageNumber),
                        signal,
                      }),
                  }),
              });
              if (!isGridApiLive(params.api)) return;
              if (!cursorPagination.current.isCurrent(requestGeneration)) {
                return;
              }
              const results = exactPage.response;
              const res = results?.data?.result;
              const newCols = normalizeConfigKeys(res?.config);

              // Merge: preserve custom columns that the backend doesn't know about
              if (newCols) {
                // Canonical order, to restore default when leaving a saved view.
                if (canonicalOrderRef)
                  canonicalOrderRef.current = newCols.map((c) => c.id);
                const currentNonCustom = (columnsRef.current || []).filter(
                  (c) => c.groupBy !== "Custom Columns",
                );
                const existingCustom = (columnsRef.current || []).filter(
                  (c) => c.groupBy === "Custom Columns",
                );
                const pending = pendingCustomColumnsRef?.current || [];
                const existingIds = new Set(existingCustom.map((c) => c.id));
                const dedupedPending = pending.filter(
                  (c) => !existingIds.has(c.id),
                );
                const newIds = new Set(newCols.map((c) => c.id));
                const currentIdSet = new Set(currentNonCustom.map((c) => c.id));
                const idSetChanged =
                  newIds.size !== currentIdSet.size ||
                  [...newIds].some((id) => !currentIdSet.has(id));
                // hasPending ensures same-tab saved-view clicks still drain
                // queued customs even when backend cols match.
                const hasPending = dedupedPending.length > 0;
                if (idSetChanged || hasPending) {
                  const allCustom = [...existingCustom, ...dedupedPending];
                  if (pending.length > 0 && pendingCustomColumnsRef) {
                    pendingCustomColumnsRef.current = [];
                  }
                  const finalNonCustom = idSetChanged
                    ? mergeNonCustomColumns(
                        currentNonCustom,
                        newCols,
                        updateObjRef.current,
                      )
                    : currentNonCustom;
                  setColumns(
                    allCustom.length > 0
                      ? [...finalNonCustom, ...allCustom]
                      : finalNonCustom,
                  );
                }
              }

              // Read columnDefs from the ref so this filter always sees the
              // post-setColumns value, not the skeleton-headed default
              // captured when dataSource was first memoized. Without the ref,
              // every page-2+ scroll fetch wrote stale skeleton headers back
              // into filteredColumnDefs.
              const currentColumnDefs = columnDefsRef.current ?? columnDefs;
              const filteredColumns = currentColumnDefs.filter((column) => {
                // Grouped columns (e.g. Annotation Metrics) always visible
                if (column.children) return true;
                if (!column.field) return true;

                // On a saved view, the view's visibleColumns (carried in
                // updateObj) is the source of truth — ignore the backend's
                // per-project default so view-specific hides don't get
                // overwritten by the data-fetch response.
                if (isOnSavedViewRef.current) {
                  return updateObjRef.current?.[column.field] ?? true;
                }

                const columnConfig = (newCols || []).find(
                  (config) => config.id === column.field,
                );
                const backendVisible = columnConfig
                  ? columnConfig.isVisible
                  : true;

                const isDefaultColumn = Object.hasOwn(
                  initialVisibility,
                  column.field,
                );
                if (
                  !isDefaultColumn &&
                  !backendVisible &&
                  updateObjRef.current?.[column.field] === true
                ) {
                  return true;
                }
                return backendVisible;
              });

              setFilteredColumnDefs(filteredColumns);
              const rows = exactPage.rows;
              const metadata = exactPage.metadata;
              if (
                resumePendingListPage({
                  page: exactPage,
                  resume: () => {
                    if (
                      cursorPagination.current.isCurrent(requestGeneration) &&
                      isGridApiLive(params.api)
                    ) {
                      params.fail();
                      if (params.api?.retryServerSideLoads) {
                        params.api.retryServerSideLoads();
                      } else {
                        params.api?.refreshServerSide?.({ purge: false });
                      }
                    }
                  },
                })
              ) {
                return;
              }
              const listReadMessage = getListReadMessage({
                result: { table: rows, metadata },
              });
              if (listReadMessage) throw new Error(listReadMessage);

              const isLastPage = exactPage.isLastPage;
              // A terminal cursor is an exact exhaustion proof. Normalize an
              // older/stale lower-bound marker so the status bar and AG Grid
              // agree with `has_more: false` instead of showing a phantom ≥N.
              const totalMetadata =
                isLastPage && metadata?.has_more === false
                  ? {
                      ...metadata,
                      total_rows: request.startRow + rows.length,
                      total_rows_is_lower_bound: false,
                    }
                  : metadata;
              const totalState = getListTotalState(totalMetadata);
              params.api.totalRowCount = totalState.totalRowCount;
              params.api.totalRowCountLowerBound =
                totalState.totalRowCountLowerBound;
              params.api.totalRowCountIsLowerBound =
                totalState.totalRowCountIsLowerBound;
              useSessionsGridStore.setState(totalState);

              const discoveredRowCount = publishPage({
                request,
                rows,
                isLastPage,
              });

              params.success({
                rowData: rows,
                rowCount: discoveredRowCount,
              });
              pageLoadSucceeded = true;
              pageLoadRowCount = rows.length;
              setContinuationNotice(null);
            } catch (error) {
              if (isExpectedRequestCancellation(error)) {
                return;
              }
              if (!isGridApiLive(params.api)) return;
              if (
                requestGeneration !== null &&
                !cursorPagination.current.isCurrent(requestGeneration)
              ) {
                return;
              }
              if (isListCursorContinuationLimitError(error)) {
                // Preserve the exact continuation checkpoint and any rows
                // already rendered. This bounded pause is neutral and only a
                // deliberate refresh/retry resumes the next exact segment.
                setContinuationNotice(true);
                params.fail();
                return;
              }
              if (
                cursorPagination.current.canRecoverFromContinuationError(
                  pageNumber,
                  error,
                )
              ) {
                inFlightPageLoads.current.clear();
                cursorPagination.current.disableCursor();
                params.fail();
                params.api?.refreshServerSide?.({ purge: true });
                return;
              }
              setContinuationNotice(null);
              enqueueSnackbar(
                "Session data could not be loaded. Please retry.",
                {
                  variant: "error",
                },
              );
              // Preserve any previously rendered rows on a failed read. The
              // default AG Grid no-rows overlay would incorrectly present a
              // degraded/error response as an exact empty result; the retry
              // snackbar above is the explicit failure state instead.
              params.fail();
            } finally {
              activeListReadsRef.current = Math.max(
                0,
                activeListReadsRef.current - 1,
              );
              finishPageLoad(pageLoadRequestId, {
                succeeded: pageLoadSucceeded,
                rowCount: pageLoadRowCount,
              });
            }
          },
          getRowId: ({ data }) => {
            return data.session_id;
          },
        };
      },
      // Semantic inputs are serialized above so referential-only parent
      // renders do not reset the cursor chain or the visible page.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [beginPageLoad, finishPageLoad, paginationRequestKey, publishPage],
    );

    const [finalColumnDefs, setFinalColumnDefs] = useState([]);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
      setIsLoading(true);
      setFinalColumnDefs(filteredColumnDefs);
      setIsLoading(false);
    }, [filteredColumnDefs]);

    useEffect(() => {
      if (columnDefs.length > 0) {
        setIsLoading(true);
        const updatedColumns = columnDefs.filter((col) => {
          // Grouped columns (e.g. Annotation Metrics) don't have a field
          if (col.children) return true;
          // New columns not yet in updateObj default to visible
          return updateObj[col.field] ?? true;
        });
        setFinalColumnDefs(updatedColumns);
        setIsLoading(false);
      }
    }, [updateObj, columnDefs]);

    const [statusBar] = useState({
      statusPanels: [
        {
          statusPanel: TotalRowsStatusBar,
          align: "left",
        },
      ],
    });

    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;
        if (!isGridApiLive(params.api)) return;
        // User drags only; programmatic moves would loop with the order re-apply.
        if (params.source !== "uiColumnMoved") return;

        const newOrder = params.api
          .getColumnState()
          .map((s) => s.colId)
          .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);

        if (!columns || !Array.isArray(columns)) return;

        const byId = new Map(columns.map((c) => [c.id, c]));
        const reordered = newOrder.map((id) => byId.get(id)).filter(Boolean);
        const matched = new Set(newOrder);
        const unmatched = columns.filter((c) => !matched.has(c.id));
        const next = [...reordered, ...unmatched];

        const changed =
          next.length !== columns.length ||
          next.some((c, i) => c.id !== columns[i]?.id);
        if (changed) {
          onUserReorder?.();
          setColumns(next);
        }
      },
      [columns, setColumns, onUserReorder],
    );

    const onRowClicked = (event) => {
      if (!event.data) {
        return;
      }

      setCurrentRowData(event.data);
      setOpen(true);
      trackEvent(Events.observeSessionidClicked);
    };

    return (
      <>
        {isLoading || finalColumnDefs === null ? (
          <LinearProgress />
        ) : (
          <Box
            className="ag-theme-quartz"
            sx={{
              paddingX: theme.spacing(2),
              paddingBottom: theme.spacing(1),
              flex: 1,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
            }}
          >
            <ListCursorContinuationNotice
              pending={Boolean(continuationNotice)}
              onContinue={continueCursorSearch}
            />
            <Box
              ref={gridElementRef}
              className={`ag-theme-quartz ${className} ${cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}`}
              style={{ flex: 1, minHeight: 0 }}
            >
              <AgGridReact
                key={`session-grid-${pageSize}`}
                ref={gridApiRef}
                columnDefs={finalColumnDefs}
                rowHeight={
                  userTraceRowHeightMapping[cellHeight]?.height ??
                  userTraceRowHeightMapping.Short.height
                }
                statusBar={statusBar}
                rowSelection={{ mode: "multiRow", enableClickSelection: false }}
                className={`clean-data-table${continuationNotice ? " ag-grid-cursor-paused" : ""}`}
                theme={agTheme}
                rowModelType="serverSide"
                serverSideDatasource={dataSource}
                pagination={true}
                paginationPageSize={pageSize}
                paginationPageSizeSelector={false}
                suppressPaginationPanel={true}
                cacheBlockSize={pageSize}
                maxBlocksInCache={OBSERVE_GRID_MAX_BLOCKS_IN_CACHE}
                maxConcurrentDatasourceRequests={
                  OBSERVE_GRID_MAX_CONCURRENT_REQUESTS
                }
                rowBuffer={5}
                // CursorGridPagination owns explicit navigation feedback.
                // Blocking AG Grid here prevents the target rows from painting
                // and creates a circular page-transition dependency.
                loading={false}
                suppressServerSideFullWidthLoadingRow={true}
                noRowsOverlayComponent={
                  continuationNotice ? () => null : undefined
                }
                serverSideInitialRowCount={pageSize}
                defaultColDef={defaultColDef}
                rowStyle={{ cursor: "pointer" }}
                onRowClicked={onRowClicked}
                onColumnMoved={onColumnMoved}
                onSelectionChanged={onSelectionChanged}
                getRowId={({ data }) => data.session_id}
                onFirstDataRendered={({ api }) => {
                  withLiveGridApi(api, (liveApi) => {
                    liveApi.setServerSideSelectionState({
                      selectAll: selectAll,
                      toggledNodes: toggledNodes,
                    });
                  });
                }}
                onModelUpdated={({ api }) => {
                  if (!selectAll && !toggledNodes?.length) {
                    withLiveGridApi(api, (liveApi) => liveApi.deselectAll());
                    return;
                  }
                }}
                onGridReady={onGridReady}
              />
            </Box>
            <CursorGridPagination
              disabled={isPageLoading || Boolean(continuationNotice)}
              loading={isPageLoading}
              page={page}
              pageCount={pageCount}
              pageSize={pageSize}
              onPageChange={goToPage}
              onPageSizeChange={changePageSize}
            />
            {currentRowData ? (
              <TracesDrawer
                open={open}
                onClose={handleDrawerClose}
                rowData={currentRowData}
                userIdForUserMode={userIdForUserMode}
              />
            ) : null}
          </Box>
        )}
      </>
    );
  },
);

SessionGrid.displayName = "SessionGrid";

SessionGrid.propTypes = {
  updateObj: PropTypes.objectOf(PropTypes.bool).isRequired,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  onUserReorder: PropTypes.func,
  filters: PropTypes.array,
  onGridReady: PropTypes.func,
  projectId: PropTypes.string,
  cellHeight: PropTypes.string,
  onSelectionChanged: PropTypes.func,
  className: PropTypes.string,
  pendingCustomColumnsRef: PropTypes.object,
  canonicalOrderRef: PropTypes.object,
  isOnSavedView: PropTypes.bool,
  userIdForUserMode: PropTypes.string,
};

export default SessionGrid;
