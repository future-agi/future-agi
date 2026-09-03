import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Box, useTheme, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import useUsersStore from "./Store/usersStore";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import {
  getUsersColumnConfig,
  userTraceRowHeightMapping,
  buildUsersRequestFilters,
} from "./common";
import { mergeCellStyle } from "../LLMTracing/common";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import PropTypes from "prop-types";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import { APP_CONSTANTS } from "src/utils/constants";
import {
  createListCursorPagination,
  isListCursorContinuationLimitError,
  LIST_CURSOR_MODES,
  loadExactListPage,
  retryServerSideCursorLoad,
  resumePendingListPage,
} from "../LLMTracing/listCursorPagination";
import {
  boundObserveListRow,
  compactObserveListResponse,
} from "../LLMTracing/observeListPayload";
import ListCursorContinuationNotice from "../LLMTracing/ListCursorContinuationNotice";
import {
  failServerSideGridRead,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import { getListReadMessage } from "../LLMTracing/listTotalMetadata";
import {
  isUserGlobalSortSupported,
  sanitizeUserSortModel,
} from "./userSortContract";
import { isExpectedRequestCancellation } from "src/utils/cacheUtils";
import { isGridApiLive, withLiveGridApi } from "src/utils/gridApi";
import {
  OBSERVE_GRID_MAX_BLOCKS_IN_CACHE,
  OBSERVE_GRID_MAX_CONCURRENT_REQUESTS,
} from "src/config/runtime_limits";
import {
  dispatchObservePageChanged,
  OBSERVE_LIST_REFRESH_EVENT,
} from "../observeEvents";

const getUsersGridThemeParams = (theme) => ({
  columnBorder: false,
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

const userRowIdentity = (row) => {
  const id = row?.end_user_id || row?.user_id || row?.id;
  return id ? `${row?.project_id || ""}:${id}` : null;
};

const UsersGrid = React.memo(
  ({
    hasActiveFilter,
    setHasData,
    setIsLoading,
    setSearchState,
    cellHeight,
  }) => {
    const theme = useTheme();
    const gridThemeParams = useMemo(
      () => getUsersGridThemeParams(theme),
      [theme],
    );
    const agTheme = useAgThemeWith(gridThemeParams);
    const gridApiRef = useRef(null);
    const activeListReadsRef = useRef(0);
    const cursorPagination = useRef(
      createListCursorPagination({
        pageParam: "current_page_index",
        pageOffset: 0,
      }),
    );
    const cursorQueryKeyRef = useRef(null);
    const [readError, setReadError] = useState(null);
    const [continuationNotice, setContinuationNotice] = useState(null);
    const continueCursorSearch = useCallback(() => {
      if (!continuationNotice) return;
      if (retryServerSideCursorLoad(gridApiRef.current?.api)) {
        setContinuationNotice(null);
      }
    }, [continuationNotice]);
    const {
      setGridApi,
      searchQuery,
      selectedAll,
      selectedRowsData,
      setSelectedAll,
      setSelectedRowsData,
      clearSelection,
      columns,
      setColumns,
      filters,
    } = useUsersStore();

    const userFirstRef = useRef(true);

    const { observeId } = useParams();
    const updatedObserveId = observeId;
    const sortStorageKey = `ag-grid-sort-model-${updatedObserveId}`;
    const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

    const validatedFilters = useMemo(
      () => buildUsersRequestFilters(filters),
      [filters],
    );

    const navigate = useNavigate();

    useEffect(() => {
      const refreshRows = () => {
        const currentPage =
          Number(gridApiRef.current?.api?.paginationGetCurrentPage?.()) + 1;
        if (Number.isSafeInteger(currentPage) && currentPage > 1) {
          dispatchObservePageChanged(currentPage);
          return;
        }
        if (activeListReadsRef.current > 0) return;
        withLiveGridApi(gridApiRef.current?.api, (api) =>
          api.refreshServerSide?.({ purge: false }),
        );
      };
      window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshRows);
      return () =>
        window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshRows);
    }, []);

    useEffect(() => {
      const initial = getUsersColumnConfig();

      const transformed = initial.map((col) => ({
        id: col.field,
        name: col.headerName || "",
        isVisible: col.hide === undefined ? true : !col.hide,
        groupBy: null,
        outputType: null,
      }));

      setColumns(transformed);
    }, []);

    const userColumnDefs = useMemo(() => {
      const baseConfig = getUsersColumnConfig();

      // If columns from store isn't ready, use baseConfig directly
      if (!columns || !Array.isArray(columns)) {
        return baseConfig.map((col) => ({
          ...col,
          colId: col.field,
          sortable: isUserGlobalSortSupported(col.field),
          hide: col.hide || false,
          lockVisible: false,
          minWidth: col?.minWidth ?? 120,
        }));
      }

      const buildColDef = (col) => {
        const originalCol = baseConfig.find((c) => c.field === col.id);

        // Custom (attribute-based) columns have no entry in baseConfig —
        // build a fallback col def that reads `data[col.id]`. Values for
        // these keys are not yet returned by getUsersList; see
        // plans/delegated-marinating-flame.md "Known limitation".
        if (!originalCol && col.groupBy === "Custom Columns") {
          return {
            headerName: col.name || col.id,
            field: col.id,
            colId: col.id,
            hide: !col.isVisible,
            lockVisible: false,
            sortable: false,
            minWidth: 160,
            flex: 1,
            valueGetter: (params) => params.data?.[col.id] ?? null,
            valueFormatter: (params) =>
              params.value === null || params.value === undefined
                ? "—"
                : String(params.value),
          };
        }

        return {
          ...originalCol,
          colId: col.id,
          sortable: isUserGlobalSortSupported(col.id),
          hide: !col.isVisible,
          lockVisible: false,
          minWidth: originalCol?.minWidth ?? 120,
        };
      };

      // Custom columns flat (ungrouped), in store order.
      const result = [];
      for (const c of columns) {
        if (c?.groupBy === "Custom Columns") {
          const colDef = buildColDef(c);
          result.push({
            ...colDef,
            minWidth: 200,
            flex: 1,
            cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
          });
          continue;
        }
        result.push(buildColDef(c));
      }

      return result;
    }, [columns]);

    const requestedProjection = useMemo(() => {
      // Zustand starts with an empty list and is hydrated in an effect. AG Grid
      // may ask its initial datasource for rows before that effect-driven store
      // update is visible, so derive the first projection from the canonical
      // column config instead of explicitly requesting no metrics.
      const projectionColumns =
        Array.isArray(columns) && columns.length > 0
          ? columns
          : getUsersColumnConfig().map((column) => ({
              id: column.field,
              isVisible: column.hide !== true,
              groupBy: null,
            }));
      const visible = projectionColumns.filter(
        (column) => column?.isVisible !== false,
      );
      return {
        requestedColumns: visible
          .filter((column) => column?.groupBy !== "Custom Columns")
          .map((column) => column.id)
          .filter(Boolean),
        attributeKeys: visible
          .filter((column) => column?.groupBy === "Custom Columns")
          .map((column) => column.id)
          .filter(Boolean),
      };
    }, [columns]);

    const dataSource = useMemo(() => {
      cursorPagination.current.reset();
      cursorQueryKeyRef.current = null;

      return {
        getRows: async (params) => {
          let pageNumber = 0;
          let requestGeneration = null;
          let continuationPending = false;
          try {
            if (!isGridApiLive(params.api)) return;
            activeListReadsRef.current += 1;
            setIsLoading(true);
            params.api.hideOverlay();
            const { request } = params;
            const pageSize = request.endRow - request.startRow;
            pageNumber = Math.floor(request.startRow / pageSize);
            if (userFirstRef.current) {
              const savedSort = localStorage.getItem(sortStorageKey);
              if (savedSort) {
                let parsedSortModel = [];
                try {
                  parsedSortModel = JSON.parse(savedSort);
                } catch {
                  // A corrupt browser preference must not fail the data read.
                }
                const sortModel = sanitizeUserSortModel(parsedSortModel);
                if (sortModel.length > 0) {
                  localStorage.setItem(
                    sortStorageKey,
                    JSON.stringify(sortModel),
                  );
                } else {
                  localStorage.removeItem(sortStorageKey);
                }
                params.api.applyColumnState({
                  state: sortModel,
                  defaultState: { sort: null },
                });
              }
              userFirstRef.current = false;
            }
            const requestedSortModel = Array.isArray(request.sortModel)
              ? request.sortModel
              : [];
            const supportedSortModel =
              sanitizeUserSortModel(requestedSortModel);
            if (
              JSON.stringify(supportedSortModel) !==
              JSON.stringify(requestedSortModel)
            ) {
              // Saved AG Grid/view state may predate the exact Users sort
              // contract. Clear only unsupported sorts so the header never
              // claims an ordering the server did not execute.
              params.api.applyColumnState({
                state: supportedSortModel,
                defaultState: { sort: null },
              });
              if (supportedSortModel.length > 0) {
                localStorage.setItem(
                  sortStorageKey,
                  JSON.stringify(supportedSortModel),
                );
              } else {
                localStorage.removeItem(sortStorageKey);
              }
            }
            const sortParams =
              supportedSortModel.length > 0
                ? supportedSortModel.map(({ colId, sort }) => ({
                    column_id: colId,
                    direction: sort,
                  }))
                : [];
            const useCursorPagination = sortParams.length === 0;
            // Mirror the active sort into the store so the export button (in the
            // Observe header) can carry the same sort the grid is showing.
            useUsersStore.setState({ sortParams });
            const queryKey = JSON.stringify({
              projectId: updatedObserveId || null,
              search: debouncedSearchQuery || "",
              filters: validatedFilters,
              sort: sortParams,
              requestedColumns: requestedProjection.requestedColumns,
              attributeKeys: requestedProjection.attributeKeys,
              pageSize,
            });
            if (cursorQueryKeyRef.current !== queryKey) {
              cursorPagination.current.reset();
              // The bounded users cursor has one deterministic candidate order.
              // Explicit AG Grid sorts retain the existing numbered/exact path;
              // mixing a sort with an opaque cursor would change row order.
              if (!useCursorPagination) {
                cursorPagination.current.disableCursor();
              }
              cursorQueryKeyRef.current = queryKey;
            }
            requestGeneration = cursorPagination.current.generation();

            const buildBaseParams = () => ({
              // Omit project_id when there's no project context — the
              // backend handles project_id=null as org-scoped, used by
              // the cross-project users page at /dashboard/users.
              ...(updatedObserveId ? { project_id: updatedObserveId } : {}),
              sort_params: JSON.stringify(sortParams),
              search: debouncedSearchQuery?.length
                ? debouncedSearchQuery
                : null,
              page_size: pageSize,
              filters: JSON.stringify(validatedFilters),
              requested_columns: JSON.stringify(
                requestedProjection.requestedColumns,
              ),
              attribute_keys: JSON.stringify(requestedProjection.attributeKeys),
            });
            const buildParams = (page) =>
              cursorPagination.current.requestParams(page, buildBaseParams());

            let results;
            let exactPage = null;
            if (useCursorPagination) {
              exactPage = await loadExactListPage({
                pagination: cursorPagination.current,
                pageNumber,
                targetRowCount: pageSize,
                loadResponse: (signal) =>
                  axios.get(endpoints.project.getUsersList(), {
                    params: buildParams(pageNumber),
                    signal,
                  }),
                rowsFromResponse: (response) =>
                  (response?.data?.result?.table || []).map(
                    boundObserveListRow,
                  ),
                metadataFromResponse: (response) =>
                  response?.data?.result?.metadata ||
                  response?.data?.result ||
                  {},
                compactResponse: compactObserveListResponse,
                rowIdentity: userRowIdentity,
                isCurrent: () =>
                  cursorPagination.current.isCurrent(requestGeneration),
                nextResponse: (_cursor, signal) =>
                  axios.get(endpoints.project.getUsersList(), {
                    params: buildParams(pageNumber),
                    signal,
                  }),
              });
              results = exactPage.response;
            } else {
              results = await axios.get(endpoints.project.getUsersList(), {
                params: buildParams(pageNumber),
              });
            }
            if (!isGridApiLive(params.api)) return;
            if (!cursorPagination.current.isCurrent(requestGeneration)) {
              return;
            }

            const res = results?.data?.result || {};
            const userData = exactPage?.rows || res?.table || [];
            if (
              useCursorPagination &&
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
              continuationPending = true;
              return;
            }
            const listReadMessage = getListReadMessage({
              result: {
                table: userData,
                metadata: exactPage?.metadata || res,
              },
            });
            if (listReadMessage) throw new Error(listReadMessage);

            const hasResults = userData.length > 0;
            if (pageNumber === 0) setHasData(hasResults);
            else if (hasResults) setHasData(true);

            const reportedTotal = Number(res?.total_count);
            const total =
              Number.isFinite(reportedTotal) && reportedTotal >= 0
                ? Math.floor(reportedTotal)
                : request.startRow + userData.length;
            const isLastPage = useCursorPagination
              ? exactPage.isLastPage
              : Number.isFinite(reportedTotal) && reportedTotal >= 0
                ? request.startRow + userData.length >= total
                : userData.length < pageSize;
            const countIsLowerBound =
              res?.count_is_lower_bound === true ||
              res?.total_count_is_lower_bound === true;
            const exactTotal = countIsLowerBound ? null : total;
            const lowerBoundTotal = countIsLowerBound ? total : null;
            const gridRowCount = isLastPage
              ? request.startRow + userData.length
              : useCursorPagination &&
                  cursorPagination.current.mode() === LIST_CURSOR_MODES.CURSOR
                ? request.endRow + 1
                : Math.max(total, request.endRow + 1);

            setReadError(null);
            setContinuationNotice(null);

            if (pageNumber === 0 && !hasResults) {
              params.api.showNoRowsOverlay();
            } else {
              params.api.hideOverlay();
            }

            if (pageNumber === 0) {
              if (debouncedSearchQuery === "") {
                if (hasActiveFilter) {
                  setSearchState("searching");
                } else {
                  setSearchState(hasResults ? "idle" : "empty");
                }
              } else {
                setSearchState("searching");
              }
            }

            // Merge new total into AG Grid's context
            const existingContext = params.api.getGridOption("context") || {};
            params.api.setGridOption("context", {
              ...existingContext,
              totalRowCount: exactTotal,
              totalRowCountLowerBound: lowerBoundTotal,
              totalRowCountIsLowerBound: countIsLowerBound,
            });

            // Clear selection only after an exact, successful first-page empty
            // result. A failed or stale page must preserve the current grid.
            if (
              pageNumber === 0 &&
              isLastPage &&
              !countIsLowerBound &&
              userData.length === 0
            ) {
              clearSelection();
            }

            params.success({
              rowData: userData,
              rowCount: gridRowCount,
            });
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
              // Keep existing rows and the exact signed checkpoint. The user
              // can explicitly retry without seeing a false empty or error
              // state, and the client never drains an unbounded cursor chain.
              setReadError(null);
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
              cursorPagination.current.disableCursor();
              setReadError(null);
              params.fail();
              params.api?.refreshServerSide?.({ purge: true });
              return;
            }
            setContinuationNotice(null);
            setReadError(QUERY_FAILED_RETRY_MESSAGE);
            setSearchState("error");
            failServerSideGridRead(params);
          } finally {
            activeListReadsRef.current = Math.max(
              0,
              activeListReadsRef.current - 1,
            );
            if (!continuationPending) setIsLoading(false);
          }
        },
      };
    }, [
      updatedObserveId,
      debouncedSearchQuery,
      validatedFilters,
      clearSelection,
      setHasData,
      setIsLoading,
      setSearchState,
      hasActiveFilter,
      sortStorageKey,
      requestedProjection,
    ]);

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellStyle: {
          padding: "0px 20px",
          fontSize: "14px",
          height: "100%",
          display: "flex",
          alignItems: "center",
        },
      }),
      [],
    );

    const onCellClicked = useCallback(
      (event) => {
        const colId = event?.colDef?.colId;
        if (colId === "actions") return;
        if (colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          return;
        }

        const userId = event.data?.user_id;
        if (!userId) return;

        // All user-detail navigation goes through the cross-project page.
        // It accepts a single user id and shows that user's traces +
        // sessions across every project in the org.
        navigate(`/dashboard/users/${encodeURIComponent(userId)}`);
      },
      [navigate],
    );

    const onColumnHeaderClicked = useCallback(
      (event) => {
        if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
          return;

        const api = event.api;
        if (!isGridApiLive(api)) return;
        if (selectedAll) {
          api.deselectAll();
          clearSelection();
        } else {
          api.selectAll();
          setSelectedAll(true);
        }
      },
      [selectedAll, setSelectedAll, clearSelection],
    );

    const onSelectionChanged = useCallback(() => {
      if (!gridApiRef.current) return;

      const api = gridApiRef.current.api;
      if (!isGridApiLive(api)) return;
      const selectedNodes = api.getSelectedNodes();
      const selectedData = selectedNodes.map((node) => node.data);

      const total = api.getGridOption("context")?.totalRowCount || 0;
      setSelectedRowsData(selectedData);
      setSelectedAll(selectedData.length === total && total > 0);
    }, [setSelectedAll, setSelectedRowsData]);

    const onGridReady = useCallback(
      (params) => {
        if (!isGridApiLive(params.api)) return;
        gridApiRef.current = params;
        setGridApi(params.api); // Store the grid API reference

        // Initial sync of selection state
        if (selectedRowsData.length > 0) {
          params.api.forEachNode((node) => {
            const isSelected = selectedRowsData.some(
              (row) => row.id === node.data.id,
            );
            node.setSelected(isSelected);
          });
        }
      },
      [selectedRowsData, setGridApi],
    );

    const containerStyle = useMemo(
      () => ({
        flexGrow: 1,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }),
      [],
    );

    const gridWrapperStyle = useMemo(
      () => ({
        paddingBottom: theme.spacing(1),
        flex: 1,
        width: "100%",
        overflow: "auto",
        minWidth: 0,
      }),
      [theme],
    );
    const fullHeightStyle = useMemo(
      () => ({
        height: "100%",
        "& .ag-cell:not([col-id='ag-Grid-SelectionColumn'])": {
          display: "flex",
          alignItems: "center",
          padding: 0,
        },
        "& .ag-cell:not([col-id='ag-Grid-SelectionColumn']) .ag-cell-wrapper": {
          display: "flex",
          alignItems: "center",
          height: "100%",
          width: "100%",
          flex: 1,
        },
        "& .ag-cell[col-id='ag-Grid-SelectionColumn']": {
          display: "flex",
          alignItems: "center",
        },
      }),
      [],
    );
    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;
        if (!isGridApiLive(params.api)) return;
        // User drags only; programmatic moves would feed back into setColumns.
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
        if (changed) setColumns(next);
      },
      [columns, setColumns],
    );

    const onSortChanged = (params) => {
      if (!isGridApiLive(params.api)) return;
      const requestedSortModel = params.api
        .getColumnState()
        .filter((col) => col.sort != null)
        .map((col) => ({
          colId: col.colId,
          sort: col.sort,
        }));
      const sortModel = sanitizeUserSortModel(requestedSortModel);

      if (sortModel.length !== requestedSortModel.length) {
        params.api.applyColumnState({
          state: sortModel,
          defaultState: { sort: null },
        });
      }

      if (sortModel.length > 0) {
        localStorage.setItem(sortStorageKey, JSON.stringify(sortModel));
      } else {
        localStorage.removeItem(sortStorageKey);
      }
    };
    return (
      <Box sx={containerStyle}>
        <ListCursorContinuationNotice
          pending={Boolean(continuationNotice)}
          onContinue={continueCursorSearch}
        />
        <Box
          className={`ag-theme-quartz ${cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}`}
          sx={gridWrapperStyle}
        >
          <Box className="ag-theme-quartz" sx={fullHeightStyle}>
            <AgGridReact
              className={`clean-data-table${continuationNotice ? " ag-grid-cursor-paused" : ""}`}
              ref={(params) => {
                gridApiRef.current = params;
              }}
              onSortChanged={onSortChanged}
              onColumnMoved={onColumnMoved}
              columnDefs={userColumnDefs}
              serverSideDatasource={dataSource}
              getRowId={({ data }) => userRowIdentity(data)}
              headerHeight={40}
              rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
              theme={agTheme}
              rowSelection={{ mode: "multiRow", enableClickSelection: false }}
              pagination={true}
              paginationPageSize={25}
              rowModelType="serverSide"
              cacheBlockSize={25}
              maxBlocksInCache={OBSERVE_GRID_MAX_BLOCKS_IN_CACHE}
              maxConcurrentDatasourceRequests={
                OBSERVE_GRID_MAX_CONCURRENT_REQUESTS
              }
              paginationPageSizeSelector={[10, 25, 50, 100]}
              defaultColDef={defaultColDef}
              onColumnHeaderClicked={onColumnHeaderClicked}
              rowStyle={{ cursor: "pointer" }}
              suppressAutoSize={true}
              suppressServerSideFullWidthLoadingRow={true}
              serverSideInitialRowCount={5}
              animateRows={true}
              getMainMenuItems={(params) =>
                params.defaultItems.filter((item) => item !== "columnChooser")
              }
              onCellClicked={onCellClicked}
              onRowSelected={onSelectionChanged}
              onGridReady={onGridReady}
              onPaginationChanged={({ api }) => {
                const page = Number(api?.paginationGetCurrentPage?.()) + 1;
                if (Number.isSafeInteger(page) && page > 1) {
                  dispatchObservePageChanged(page);
                }
              }}
              noRowsOverlayComponent={() =>
                continuationNotice
                  ? null
                  : NoRowsOverlay(
                      <Typography
                        role={readError ? "alert" : undefined}
                        typography="m3"
                        color="text.primary"
                        fontWeight="fontWeightMedium"
                      >
                        {readError || "No active users for current filters"}
                      </Typography>,
                    )
              }
            />
          </Box>
        </Box>
      </Box>
    );
  },
);

UsersGrid.displayName = "UsersGrid";

UsersGrid.propTypes = {
  hasActiveFilter: PropTypes.bool,
  setHasData: PropTypes.func,
  setIsLoading: PropTypes.func,
  setSearchState: PropTypes.func,
  cellHeight: PropTypes.string,
};

export default UsersGrid;
