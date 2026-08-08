/* eslint-disable react/prop-types */
import { Box, Typography, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { getRandomId } from "src/utils/utils";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import {
  AllowedGroups,
  applyQuickFilters,
  TRACE_DEFAULT_COLUMNS,
  getTraceListColumnDefs,
  FILTER_FOR_HAS_EVAL,
  generateAnnotationColumnsForTracing,
  normalizeConfigKeys,
  toBackendFilters,
} from "./common";
import { RENDERER_CONFIG } from "./Renderers/common";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { userTraceRowHeightMapping } from "../UsersView/common";
import { statusBar } from "src/components/run-insights/traces-tab/common";
import LLMTracingTraceDetailDrawer from "./LLMTracingTraceDetailDrawer";
import { useLLMTracingStoreShallow, useTraceGridStore } from "./states";
import { APP_CONSTANTS } from "src/utils/constants";
import { useReplaySessionsStoreShallow } from "../SessionsView/ReplaySessions/store";
import { REPLAY_MODULES } from "../SessionsView/ReplaySessions/configurations";
import { useShallowToggleAnnotationsStore } from "../../agents/store";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  failServerSideGridRead,
  getQueryReadState,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import {
  createListCursorPagination,
  isListCursorContinuationLimitError,
  loadExactListPage,
  retryServerSideCursorLoad,
  resumePendingListPage,
} from "./listCursorPagination";
import ListCursorContinuationNotice from "./ListCursorContinuationNotice";
import { getListReadMessage, getListTotalState } from "./listTotalMetadata";
import { getTraceAttributeRequestKey } from "./traceAttributeRequest";

const ROWS_LIMIT = 25;
const traceRowIdentity = (row) => {
  const id = row?.trace_id || row?.id;
  return id ? `${row?.project_id || ""}:${id}` : null;
};
const EMPTY_EXTRA_FILTERS = [];

const TraceGrid = React.forwardRef(
  (
    {
      filters,
      extraFilters,
      columns,
      setColumns,
      setFilters,
      setExtraFilters,
      setFilterOpen,
      setLoading,
      projectId,
      cellHeight,
      hasEvalFilter,
      metricFilters,
      pendingCustomColumnsRef,
      canonicalOrderRef,
      enabled = true,
      showErrors = false,
    },
    gridRef,
  ) => {
    const agTheme = useAgTheme();
    const theme = useTheme();
    const [dateInterval] = useUrlState("dateInterval", "day");
    const { openReplaySessionDrawer, currentStep, validatedSteps } =
      useReplaySessionsStoreShallow((state) => ({
        openReplaySessionDrawer: state.openReplaySessionDrawer,
        currentStep: state.currentStep,
        validatedSteps: state.validatedSteps,
      }));

    const {
      traceDetailDrawerOpen,
      setTraceDetailDrawerOpen,
      setVisibleTraceIds,
    } = useLLMTracingStoreShallow((state) => ({
      traceDetailDrawerOpen: state.traceDetailDrawerOpen,
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
      setVisibleTraceIds: state.setVisibleTraceIds,
    }));
    const activeTraceId = traceDetailDrawerOpen?.traceId || null;
    const [openQuickFilter, setOpenQuickFilter] = useState(null);
    const [selectedAll, setSelectedAll] = useState(false);
    const [readMessage, setReadMessage] = useState(null);
    const readMessageRef = useRef(null);
    const [continuationNotice, setContinuationNotice] = useState(null);
    const [gridLoading, setGridLoading] = useState(enabled);
    const firstPageRequestRef = useRef(0);
    const preserveRowsDuringNextRefreshRef = useRef(false);

    // Use ref to track latest columns for comparison without triggering dataSource recreation
    const columnsRef = useRef(columns);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);
    const requestedAttributeKeysKey = useMemo(
      () => getTraceAttributeRequestKey(columns),
      [columns],
    );

    // Prefetch cache: stores next page data so scroll feels instant
    const prefetchCache = useRef(new Map());
    const cursorPagination = useRef(createListCursorPagination());
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));
    const refreshGrid = useCallback(
      (purge = true) => {
        prefetchCache.current.clear();
        cursorPagination.current.reset();
        preserveRowsDuringNextRefreshRef.current = !purge;
        if (purge) setGridLoading(enabled);
        gridRef?.current?.api?.refreshServerSide({ purge });
      },
      [enabled, gridRef],
    );
    const continueCursorSearch = useCallback(() => {
      if (!continuationNotice) return;
      if (retryServerSideCursorLoad(gridRef?.current?.api)) {
        setContinuationNotice(null);
      }
    }, [continuationNotice, gridRef]);
    const filterRequestKey = useMemo(
      () =>
        JSON.stringify({
          filters,
          extraFilters: extraFilters || EMPTY_EXTRA_FILTERS,
          metricFilters: metricFilters || [],
          hasEvalFilter,
          dateInterval,
          projectId,
          requestedAttributeKeys: requestedAttributeKeysKey,
          enabled,
        }),
      [
        filters,
        extraFilters,
        metricFilters,
        hasEvalFilter,
        dateInterval,
        projectId,
        requestedAttributeKeysKey,
        enabled,
      ],
    );
    const previousFilterRequestKeyRef = useRef(filterRequestKey);

    useEffect(() => {
      if (previousFilterRequestKeyRef.current === filterRequestKey) return;
      previousFilterRequestKeyRef.current = filterRequestKey;
      setGridLoading(enabled);
      prefetchCache.current.clear();
      cursorPagination.current.reset();
      refreshGrid(true);
    }, [enabled, filterRequestKey, refreshGrid]);

    // Listen for refresh events from the header reload button
    useEffect(() => {
      // A same-query manual refresh keeps the last exact rows visible until
      // their replacement is complete. Filter/range changes still purge so
      // rows from a different query are never presented as current.
      const handler = () => refreshGrid(false);
      window.addEventListener("observe-refresh", handler);
      return () => window.removeEventListener("observe-refresh", handler);
    }, [refreshGrid]);

    // Clear AG Grid's internal selection when the project changes — the
    // zustand reset handled in the header only clears our mirror, not AG
    // Grid's server-side selection model. Also reset the local
    // `selectedAll` flag so the header checkbox's next click re-triggers
    // selectAll (otherwise the stale `true` makes the first click a
    // deselect no-op).
    useEffect(() => {
      const handler = () => {
        gridRef?.current?.api?.deselectAll?.();
        setSelectedAll(false);
      };
      window.addEventListener("observe-reset-selection", handler);
      return () =>
        window.removeEventListener("observe-reset-selection", handler);
    }, [gridRef]);

    useEffect(() => {
      gridRef?.current?.api?.hideOverlay?.();
    }, [filters, extraFilters, hasEvalFilter, metricFilters, gridRef]);

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderFilterButton: true,
        suppressHeaderContextMenu: true,
        suppressMovable: false,
        flex: 1,
        minWidth: 80,
        cellStyle: {
          padding: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
        suppressSizeToFit: false,
        sortable: false,
        cellRendererParams: {
          applyQuickFilters: applyQuickFilters(
            setExtraFilters,
            setOpenQuickFilter,
            setFilterOpen,
          ),
        },
      }),
      [setFilterOpen, setExtraFilters],
    );

    const { role } = useAuthContext();
    // Viewers can browse traces but not edit tags — gate the cell affordance.
    const canEditTags = Boolean(
      RolePermission.OBSERVABILITY[PERMISSIONS.CREATE_EDIT_PROJECT]?.[role],
    );
    // Tells cell renderers (e.g. TagsCell) they are on the trace grid (so tag
    // edits target the trace, not its root span) and whether the role may edit.
    const gridContext = useMemo(
      () => ({ entityType: "trace", canEditTags }),
      [canEditTags],
    );

    const dataSource = useMemo(
      () => {
        prefetchCache.current.clear();
        cursorPagination.current.reset();
        return {
          getRows: async (params) => {
            if (!enabled) {
              // Disabled/unresolved is not an exact empty response. Reporting
              // success here makes AG Grid publish "No traces" without ever
              // calling the list API.
              params.fail();
              return;
            }
            let pageNumber = 0;
            let firstPageRequestId = null;
            let requestGeneration = null;
            let continuationPending = false;
            try {
              setLoading(true);
              params.api?.hideOverlay();
              const { request } = params;
              requestGeneration = cursorPagination.current.generation();

              const pageSize = request.endRow - request.startRow;
              pageNumber = Math.floor(request.startRow / pageSize);
              if (pageNumber === 0) {
                firstPageRequestId = ++firstPageRequestRef.current;
                const preserveExistingRows =
                  preserveRowsDuringNextRefreshRef.current;
                preserveRowsDuringNextRefreshRef.current = false;
                if (!preserveExistingRows) setGridLoading(true);
                readMessageRef.current = null;
                setReadMessage(null);
                setContinuationNotice(null);
              }

              const buildParams = (page) =>
                cursorPagination.current.requestParams(page, {
                  // Omit project_id when null — the backend treats absent
                  // project_id as org-scoped (used by the cross-project user
                  // detail page).
                  ...(projectId ? { project_id: projectId } : {}),
                  page_size: ROWS_LIMIT,
                  // JSON preserves attribute paths containing commas. The API
                  // rejects oversized requests; neither side truncates.
                  ...(requestedAttributeKeysKey === "[]"
                    ? {}
                    : { attribute_keys: requestedAttributeKeysKey }),
                  filters: JSON.stringify(
                    toBackendFilters([
                      ...filters,
                      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                      ...(extraFilters || EMPTY_EXTRA_FILTERS),
                      ...(metricFilters || []),
                    ]),
                  ),
                  ...(dateInterval && { interval: dateInterval }),
                });

              // Use prefetched data if available, otherwise fetch
              const cached = prefetchCache.current.get(pageNumber);
              prefetchCache.current.delete(pageNumber);
              const exactPage = await loadExactListPage({
                pagination: cursorPagination.current,
                pageNumber,
                targetRowCount: ROWS_LIMIT,
                loadResponse: () =>
                  cached ||
                  axios.get(endpoints.project.getTracesForObserveProject(), {
                    params: buildParams(pageNumber),
                  }),
                rowsFromResponse: (response) =>
                  response?.data?.result?.table || [],
                metadataFromResponse: (response) =>
                  response?.data?.result?.metadata || {},
                rowIdentity: traceRowIdentity,
                isCurrent: () =>
                  cursorPagination.current.isCurrent(requestGeneration),
                nextResponse: () =>
                  axios.get(endpoints.project.getTracesForObserveProject(), {
                    params: buildParams(pageNumber),
                  }),
              });
              if (!cursorPagination.current.isCurrent(requestGeneration)) {
                // A newer filter/range owns the grid now. Do not let this stale
                // response replace its loading state with an empty overlay.
                params.fail();
                return;
              }

              const results = exactPage.response;
              const res = results?.data?.result;
              const rows = exactPage.rows;
              const metadata = exactPage.metadata;
              if (
                resumePendingListPage({
                  page: exactPage,
                  resume: () => {
                    if (cursorPagination.current.isCurrent(requestGeneration)) {
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
              const nextReadState = getQueryReadState(results?.data);
              if (pageNumber === 0 || nextReadState !== "complete") {
                const nextReadMessage = getListReadMessage(results?.data);
                readMessageRef.current = nextReadMessage;
                setReadMessage(nextReadMessage);
              }
              const newCols = normalizeConfigKeys(res?.config);

              // Use ref to get latest columns for comparison without triggering dataSource recreation
              // Compare only non-custom columns to avoid unnecessary re-renders
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
                // Diff by ID set — order isn't a schema change (TH-4996).
                const newIds = new Set(newCols.map((c) => c.id));
                const currentIdSet = new Set(currentNonCustom.map((c) => c.id));
                const idSetChanged =
                  newIds.size !== currentIdSet.size ||
                  [...newIds].some((id) => !currentIdSet.has(id));
                const hasPending = dedupedPending.length > 0;
                if (idSetChanged || hasPending) {
                  const allCustom = [...existingCustom, ...dedupedPending];
                  if (pending.length > 0 && pendingCustomColumnsRef) {
                    pendingCustomColumnsRef.current = [];
                  }
                  let finalNonCustom;
                  if (idSetChanged) {
                    const newById = new Map(newCols.map((nc) => [nc.id, nc]));
                    const seen = new Set();
                    const kept = currentNonCustom
                      .filter((cc) => newById.has(cc.id))
                      .map((cc) => {
                        seen.add(cc.id);
                        return {
                          ...newById.get(cc.id),
                          isVisible: cc.isVisible,
                        };
                      });
                    const added = newCols.filter((nc) => !seen.has(nc.id));
                    finalNonCustom = [...kept, ...added];
                  } else {
                    finalNonCustom = currentNonCustom;
                  }
                  setColumns(
                    allCustom.length > 0
                      ? [...finalNonCustom, ...allCustom]
                      : finalNonCustom,
                  );
                }
              }

              const totalState = getListTotalState(metadata);
              params.api.totalRowCount = totalState.totalRowCount;
              params.api.totalRowCountLowerBound =
                totalState.totalRowCountLowerBound;
              params.api.totalRowCountIsLowerBound =
                totalState.totalRowCountIsLowerBound;
              useTraceGridStore.setState(totalState);

              // Infinite-scroll behavior: don't tell AG Grid the total upfront.
              // Use -1 (unknown) so it only extends the scrollbar as pages load.
              // When we get fewer rows than requested, that's the last page.
              const isLastPage = exactPage.isLastPage;
              const lastRow = isLastPage ? request.startRow + rows.length : -1;

              params.success({
                rowData: rows,
                rowCount: lastRow,
              });
              setContinuationNotice(null);

              if (pageNumber === 0 && rows.length === 0) {
                params.api?.showNoRowsOverlay();
              } else {
                params.api?.hideOverlay();
              }

              // Collect all loaded trace IDs for prev/next navigation
              setTimeout(() => {
                const ids = [];
                params.api.forEachNode((node) => {
                  if (node.data?.trace_id) ids.push(node.data.trace_id);
                });
                if (ids.length > 0) setVisibleTraceIds(ids);
              }, 0);

              // Prefetch next page so scroll feels instant
              if (exactPage.canPrefetch) {
                axios
                  .get(endpoints.project.getTracesForObserveProject(), {
                    params: buildParams(pageNumber + 1),
                  })
                  .then((res) => {
                    if (cursorPagination.current.isCurrent(requestGeneration)) {
                      prefetchCache.current.set(pageNumber + 1, res);
                    }
                  })
                  .catch(() => {});
              }
            } catch (error) {
              if (isListCursorContinuationLimitError(error)) {
                // Keep the signed checkpoint and any existing rows. This is a
                // bounded exact read awaiting an explicit retry, not an empty
                // result or a user-visible query failure.
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
                prefetchCache.current.clear();
                cursorPagination.current.disableCursor();
                params.fail();
                params.api?.refreshServerSide?.({ purge: true });
                return;
              }
              readMessageRef.current = QUERY_FAILED_RETRY_MESSAGE;
              setReadMessage(QUERY_FAILED_RETRY_MESSAGE);
              failServerSideGridRead(params);
            } finally {
              if (
                !continuationPending &&
                firstPageRequestId !== null &&
                firstPageRequestId === firstPageRequestRef.current &&
                cursorPagination.current.isCurrent(requestGeneration)
              ) {
                setGridLoading(false);
              }
              if (!continuationPending) setLoading(false);
            }
          },
        };
      },
      // Using columnsRef for comparison to avoid adding columns to deps
      // which would cause dataSource recreation on visibility changes
      // eslint-disable-next-line react-hooks/exhaustive-deps
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [
        filters,
        JSON.stringify(extraFilters),
        JSON.stringify(metricFilters),
        projectId,
        setLoading,
        hasEvalFilter,
        enabled,
        dateInterval,
        requestedAttributeKeysKey,
      ],
    );

    const { columnDefs } = useMemo(() => {
      // If columns are empty → return initial/default columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: TRACE_DEFAULT_COLUMNS,
          bottomRow: [],
        };
      }

      // Flat columns — no grouping for eval/annotation metrics
      const bottomRowObj = {};
      const annotationCols = columns.filter(
        (c) => c?.groupBy === "Annotation Metrics",
      );
      // Custom columns flat (ungrouped), in store order.
      const columnDefsResult = [];
      for (const c of columns) {
        if (c?.groupBy === "Annotation Metrics") continue;
        bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
        if (c?.groupBy === "Custom Columns") {
          const colDef = getTraceListColumnDefs(c);
          columnDefsResult.push({ ...colDef, minWidth: 200, flex: 1 });
          continue;
        }
        columnDefsResult.push(getTraceListColumnDefs(c));
      }

      // Add annotation columns as flat columns (not grouped)
      const annotationColumns = generateAnnotationColumnsForTracing(
        annotationCols,
        showMetricsIds,
      );
      if (annotationColumns?.length > 0) {
        // Flatten: extract children from annotation groups
        for (const group of annotationColumns) {
          if (group.children) {
            columnDefsResult.push(...group.children);
          } else {
            columnDefsResult.push(group);
          }
        }
      }
      return {
        columnDefs: columnDefsResult,
        bottomRow: [
          {
            ...bottomRowObj,
          },
        ],
      };
    }, [columns, showMetricsIds]);

    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);

    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;
        // User drags only; programmatic moves would feed back into setColumns.
        if (params.source !== "uiColumnMoved") return;

        const newOrder = params.api
          .getColumnState()
          .map((s) => s.colId)
          .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);

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
    const onSelectionChanged = useCallback((params) => {
      // In server-side row model, ssState.toggledNodes is authoritative —
      // an empty array is a valid, meaningful state (e.g. when selectAll is
      // true, [] means "no deselections, everything is selected"). Only
      // fall back to getSelectedNodes() in client-side mode.
      const isServerSide =
        typeof params.api.getServerSideSelectionState === "function";
      const ssState = isServerSide
        ? params.api.getServerSideSelectionState() || {}
        : {};
      const selectedNodes = params.api.getSelectedNodes?.() || [];
      const idsFromNodes = selectedNodes
        .map((n) => n.data?.trace_id)
        .filter(Boolean);
      const toggled = isServerSide ? ssState.toggledNodes || [] : idsFromNodes;
      useTraceGridStore.setState({
        toggledNodes: toggled,
        selectAll: !!ssState.selectAll,
      });
    }, []);

    const handleCellClick = useCallback(
      (event) => {
        if (!event?.node?.id) {
          //disguard clicks on empty rows
          return;
        }
        if (event?.column?.colId === "status") return;
        if (RENDERER_CONFIG.tagColumns.includes(event?.column?.getColId()))
          return;
        if (
          event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
        ) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          // Belt-and-suspenders: AG Grid v32+'s new rowSelection API can
          // silently drop the selectionChanged event when node.setSelected
          // is called manually in a serverSide row model. Mirror the
          // onSelectionChanged logic — trust server-side state verbatim so
          // toggling under selectAll correctly inverts the selection.
          setTimeout(() => {
            const isServerSide =
              typeof event.api.getServerSideSelectionState === "function";
            const ssState = isServerSide
              ? event.api.getServerSideSelectionState() || {}
              : {};
            const nodes = event.api.getSelectedNodes?.() || [];
            const idsFromNodes = nodes
              .map((n) => n.data?.trace_id)
              .filter(Boolean);
            const toggled = isServerSide
              ? ssState.toggledNodes || []
              : idsFromNodes;
            useTraceGridStore.setState({
              toggledNodes: toggled,
              selectAll: !!ssState.selectAll,
            });
          }, 0);
          return;
        }

        const traceId = event?.data?.trace_id;
        if (!traceId) {
          return;
        }
        setTraceDetailDrawerOpen({ traceId: traceId, filters: filters });

        // trackEvent(Events.observeTraceidClicked);
      },
      [filters, setTraceDetailDrawerOpen],
    );

    const shouldDisable = useMemo(() => {
      return (
        openReplaySessionDrawer?.[REPLAY_MODULES.TRACES] &&
        currentStep > 0 &&
        validatedSteps[currentStep - 1]
      );
    }, [openReplaySessionDrawer, currentStep, validatedSteps]);

    return (
      <Box
        sx={{
          height: "calc(100vh - 270px)",
          display: "flex",
          flexDirection: "column",
        }}
        className={cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}
      >
        {readMessage && (
          <Box
            role="status"
            sx={{
              px: 1.5,
              py: 0.75,
              fontSize: 12,
              color: "warning.main",
              bgcolor: "warning.lighter",
              borderBottom: "1px solid",
              borderColor: "warning.light",
            }}
          >
            {readMessage}
          </Box>
        )}
        <ListCursorContinuationNotice
          pending={Boolean(continuationNotice)}
          onContinue={continueCursorSearch}
        />
        <AgGridReact
          style={{ flex: 1, minHeight: 0 }}
          className={`clean-data-table ${continuationNotice ? "ag-grid-cursor-paused" : ""} ${shouldDisable ? "ag-grid-disabled" : ""}`}
          theme={agTheme.withParams({
            columnBorder: false,
            headerColumnBorder: false,
            wrapperBorder: { width: 0 },
            wrapperBorderRadius: 0,
            rowBorder: { width: 1, color: "rgba(0,0,0,0.06)" },
            headerFontSize: "13px",
            headerFontWeight: 500,
            headerBackgroundColor: "transparent",
            headerTextColor: theme.palette.text.primary,
            rowHoverColor: "rgba(120,87,252,0.04)",
          })}
          animateRows={false}
          headerHeight={40}
          ref={gridRef}
          rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          context={gridContext}
          tooltipShowDelay={0}
          tooltipHideDelay={2000}
          tooltipInteraction={true}
          rowSelection={{ mode: "multiRow", enableClickSelection: false }}
          pagination={false}
          cacheBlockSize={ROWS_LIMIT}
          maxBlocksInCache={undefined}
          rowBuffer={5}
          suppressServerSideFullWidthLoadingRow={true}
          rowModelType="serverSide"
          serverSideDatasource={dataSource}
          loading={
            gridLoading ||
            previousFilterRequestKeyRef.current !== filterRequestKey
          }
          noRowsOverlayComponent={() =>
            continuationNotice
              ? null
              : NoRowsOverlay(
                  <Typography
                    sx={{
                      fontSize: 14,
                      fontWeight: 400,
                      color: "text.secondary",
                    }}
                  >
                    {readMessageRef.current ||
                      (showErrors ? "No error found" : "No traces found")}
                  </Typography>,
                )
          }
          onCellClicked={handleCellClick}
          onSelectionChanged={onSelectionChanged}
          onColumnMoved={onColumnMoved}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
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
          statusBar={statusBar}
          blockLoadDebounceMillis={300}
          getRowId={(d) => {
            return d?.data?.trace_id;
          }}
          getRowStyle={(params) => {
            if (
              params.data?.trace_id &&
              params.data.trace_id === activeTraceId
            ) {
              return { backgroundColor: "rgba(120, 87, 252, 0.08)" };
            }
            return null;
          }}
        />
        <LLMTracingTraceDetailDrawer refreshGrid={refreshGrid} />
        <NumberQuickFilterPopover
          open={Boolean(openQuickFilter)}
          filterData={openQuickFilter}
          onClose={() => setOpenQuickFilter(null)}
          setFilters={setFilters}
          setFilterOpen={setFilterOpen}
        />
      </Box>
    );
  },
);

TraceGrid.displayName = "TraceGrid";

TraceGrid.propTypes = {
  filters: PropTypes.array,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.func,
  setLoading: PropTypes.func,
  compareType: PropTypes.string,
  projectId: PropTypes.string,
  cellHeight: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
  metricFilters: PropTypes.array,
  enabled: PropTypes.bool,
  showErrors: PropTypes.bool,
};

export default TraceGrid;
