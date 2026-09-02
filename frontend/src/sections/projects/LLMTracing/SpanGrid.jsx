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
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { getRandomId, safeParse } from "src/utils/utils";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "src/routes/hooks";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";

import {
  AllowedGroups,
  applyQuickFilters,
  FILTER_FOR_HAS_EVAL,
  SPAN_DEFAULT_COLUMNS,
  mergeCellStyle,
  generateAnnotationColumnsForTracing,
  normalizeConfigKeys,
  toBackendFilters,
} from "./common";
import CustomTraceRenderer from "./Renderers/CustomTraceRenderer";
import CustomTraceHeaderRenderer from "./Renderers/CustomTraceHeaderRenderer";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { statusBar } from "src/components/run-insights/traces-tab/common";
import LLMTracingSpanDetailDrawer from "./LLMTracingSpanDetailDrawer";
import { useLLMTracingStoreShallow, useSpanGridStore } from "./states";
import { userTraceRowHeightMapping } from "../UsersView/common";
import IPOPTooltipComponent from "./Renderers/IPOPTooltipComponent";
import { RENDERER_CONFIG } from "./Renderers/common";
import { NameCell } from "./Renderers";
import IPOPCell from "./Renderers/IPOPCell";
import { isCellValueEmpty } from "src/components/table/utils";
import { APP_CONSTANTS } from "src/utils/constants";
import { useShallowToggleAnnotationsStore } from "../../agents/store";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import {
  failServerSideGridRead,
  getQueryReadMessage,
  getQueryReadState,
} from "src/utils/queryReadState";
import {
  createListCursorPagination,
  isListCursorContinuationLimitError,
  loadExactListPage,
  retryServerSideCursorLoad,
  resumePendingListPage,
  shareInFlightListPage,
} from "./listCursorPagination";
import ListCursorContinuationNotice from "./ListCursorContinuationNotice";
import { getListTotalState } from "./listTotalMetadata";
import { getSpanPhysicalRowId } from "./spanPhysicalIdentity";
import {
  parseAxiosResult,
  parseSpanObserveListResponse,
} from "src/api/project/observe-contracts";
import {
  getCanonicalColumnSnapshot,
  mergeColumnsWithAuthoritativeConfig,
} from "./defaultColumns";
import {
  OBSERVE_GRID_MAX_BLOCKS_IN_CACHE,
  OBSERVE_GRID_MAX_CONCURRENT_REQUESTS,
} from "src/config/runtime_limits";
import {
  boundObserveListRow,
  compactObserveListResponse,
} from "./observeListPayload";
import { isExpectedRequestCancellation } from "src/utils/cacheUtils";
import { isGridApiLive, withLiveGridApi } from "src/utils/gridApi";
import CursorGridPagination from "./CursorGridPagination";
import useCursorGridPagination from "./useCursorGridPagination";
import useImmediateGridQueryTransition from "./useImmediateGridQueryTransition";
import {
  dispatchObservePageChanged,
  OBSERVE_LIST_REFRESH_EVENT,
} from "../observeEvents";

const loadSpanObservePage = (params, signal) =>
  axios
    .get(endpoints.project.getSpansForObserveProject(), { params, signal })
    .then((response) =>
      parseAxiosResult(response, parseSpanObserveListResponse),
    );

const getSpanListColumnDefs = (col) => {
  const colId = col?.id;
  const isInputOutput = colId === "input" || colId === "output";
  const isCustomColumn = col?.groupBy === "Custom Columns";

  return {
    headerName: col.name,
    ...(isCustomColumn
      ? { colId: col.id, minWidth: 180, flex: 1 }
      : {
          field: col.id,
          ...(colId === "total_tokens" ? { minWidth: 240 } : {}),
        }),
    hide: !col?.isVisible,
    context: { sourceColumn: col },
    // Custom columns use valueGetter to handle dot-notation attribute keys
    ...(isCustomColumn
      ? (() => {
          return {
            valueGetter: (params) => {
              if (!params.data) return null;
              let value = params.data[colId];
              if (value === undefined && colId.includes(".")) {
                value = colId
                  .split(".")
                  .reduce((obj, key) => obj?.[key], params.data);
              }
              if (value === undefined || value === null) return null;
              if (Array.isArray(value) || typeof value === "object") {
                return JSON.stringify(value);
              }
              return String(value);
            },
          };
        })()
      : isInputOutput
        ? {
            valueGetter: (params) => {
              const value = params.data?.[colId];
              if (isCellValueEmpty(value)) {
                return null;
              }
              if (typeof value === "object") {
                return JSON.stringify(value);
              }
              return value;
            },
          }
        : {}),
    valueFormatter: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return "-"; // shown when no renderer is used
      }
      // For input/output columns, valueGetter already normalized the value
      // so we don't need to do anything here
      return value;
    },
    cellRendererSelector: (params) => {
      const value = params.value;
      const column = params?.colDef?.context?.sourceColumn;
      const colId = column?.id;

      // The tags column stays interactive even when empty so a first tag can
      // be added via its "+ Tag" affordance. Other columns render nothing when
      // empty (valueFormatter shows "-").
      if (isCellValueEmpty(value) && colId !== "tags") {
        return null;
      }

      if (RENDERER_CONFIG.nameColumns.includes(colId)) {
        return {
          component: NameCell,
        };
      }
      if (colId === "input" || colId === "output") {
        return {
          component: IPOPCell,
        };
      }
      // Use CustomTraceRenderer for non-empty values
      return { component: CustomTraceRenderer };
    },
    cellStyle: (params) => {
      const value = params.value;
      // The tags column keeps its default left alignment so an empty "+ Tag"
      // sits where the chips will, instead of jumping from center to left.
      const cellColId = params?.colDef?.context?.sourceColumn?.id;
      if (isCellValueEmpty(value) && cellColId !== "tags") {
        return {
          display: "flex",
          alignItems: "center",
          height: "100%",
          justifyContent: "center",
        };
      }
    },
    headerComponent: CustomTraceHeaderRenderer,
    // Add tooltip for input/output columns
    ...(col?.id === "input" || col?.id === "output"
      ? {
          tooltipComponent: IPOPTooltipComponent,
          tooltipValueGetter: (params) => {
            const value = params.value;
            // Parse value according to its type - if string (JSON from valueGetter), parse to object
            // Otherwise return as is
            if (value === null || value === undefined || value === "") {
              return null;
            }
            // If value is a string, try to parse it (it might be a JSON string from valueGetter)
            if (typeof value === "string") {
              const parsed = safeParse(value);
              // If parsing succeeded and result is an object, use it; otherwise use original string
              return typeof parsed === "object" && parsed !== null
                ? parsed
                : value;
            }
            // If value is already an object, return it directly
            return value;
          },
        }
      : {}),
  };
};

const EMPTY_EXTRA_FILTERS = [];

const SpanGrid = React.forwardRef(
  (
    {
      columns,
      setColumns,
      filters,
      extraFilters,
      setFilters,
      setExtraFilters,
      setFilterOpen,
      setLoading,
      hasEvalFilter,
      cellHeight,
      metricFilters,
      pendingCustomColumnsRef,
      canonicalOrderRef,
      canonicalColumnsRef,
      enabled = true,
      compareType,
    },
    gridRef,
  ) => {
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));

    const theme = useTheme();
    const gridThemeParams = useMemo(
      () => ({
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
      }),
      [theme],
    );
    const agTheme = useAgThemeWith(gridThemeParams);
    const { observeId } = useParams();
    const { setSpanDetailDrawerOpen } = useLLMTracingStoreShallow((state) => ({
      setSpanDetailDrawerOpen: state.setSpanDetailDrawerOpen,
    }));
    const [openQuickFilter, setOpenQuickFilter] = useState(null);
    const [selectedAll, setSelectedAll] = useState(false);
    const [readState, setReadState] = useState("complete");
    const readStateRef = useRef("complete");
    const readMessage = getQueryReadMessage(readState);
    const [continuationNotice, setContinuationNotice] = useState(null);
    const [gridLoading, setGridLoading] = useState(enabled);
    const firstPageRequestRef = useRef(0);
    const preserveRowsDuringNextRefreshRef = useRef(false);
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
    } = useCursorGridPagination(gridRef, gridElementRef);

    // Use ref to track latest columns for comparison without triggering dataSource recreation
    const columnsRef = useRef(columns);
    const authoritativeConfigProjectRef = useRef(null);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);

    const inFlightPageLoads = useRef(new Map());
    const cursorPagination = useRef(createListCursorPagination());

    const refreshGrid = useCallback(
      (purge = true) => {
        inFlightPageLoads.current.clear();
        cursorPagination.current.reset();
        resetPagination();
        preserveRowsDuringNextRefreshRef.current = !purge;
        if (purge) setGridLoading(enabled);
        withLiveGridApi(gridRef?.current?.api, (api) =>
          api.refreshServerSide({ purge }),
        );
      },
      [enabled, gridRef, resetPagination],
    );
    const continueCursorSearch = useCallback(() => {
      if (!continuationNotice) return;
      if (retryServerSideCursorLoad(gridRef?.current?.api)) {
        setContinuationNotice(null);
      }
    }, [continuationNotice, gridRef]);
    const selectionQueryKey = useMemo(
      () =>
        JSON.stringify({
          filters,
          extraFilters: extraFilters || EMPTY_EXTRA_FILTERS,
          metricFilters: metricFilters || [],
          hasEvalFilter,
          observeId,
          enabled,
          compareType,
        }),
      [
        filters,
        extraFilters,
        metricFilters,
        hasEvalFilter,
        observeId,
        enabled,
        compareType,
      ],
    );
    const filterRequestKey = useMemo(
      () => JSON.stringify({ selectionQueryKey, pageSize }),
      [pageSize, selectionQueryKey],
    );
    const { handoffToFirstPageRequest, transitionLoading } =
      useImmediateGridQueryTransition({
        enabled,
        filterRequestKey,
        gridRef,
        resetPagination,
      });
    const clearSelection = useCallback(() => {
      const api = gridRef?.current?.api;
      withLiveGridApi(api, (liveApi) => {
        liveApi.deselectAll?.();
        liveApi.setServerSideSelectionState?.({
          selectAll: false,
          toggledNodes: [],
        });
      });
      setSelectedAll(false);
      useSpanGridStore.setState({
        selectAll: false,
        toggledNodes: [],
      });
    }, [gridRef]);
    const previousSelectionQueryKeyRef = useRef(selectionQueryKey);
    useEffect(() => {
      if (previousSelectionQueryKeyRef.current !== selectionQueryKey) {
        clearSelection();
      }
      previousSelectionQueryKeyRef.current = selectionQueryKey;
    }, [clearSelection, selectionQueryKey]);
    // Keep the last exact same-query rows during a manual refresh. A query-key
    // change replaces the datasource and shows a neutral loading state.
    useEffect(() => {
      const manualRefresh = () => refreshGrid(false);
      const autoRefresh = () => {
        if (!enabled) return;
        if (page > 1) {
          dispatchObservePageChanged(page);
          return;
        }
        if (inFlightPageLoads.current.size > 0) return;
        refreshGrid(false);
      };
      window.addEventListener("observe-refresh", manualRefresh);
      window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, autoRefresh);
      return () => {
        window.removeEventListener("observe-refresh", manualRefresh);
        window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, autoRefresh);
      };
    }, [enabled, page, refreshGrid]);

    // Keep the explicit reset event aligned with query-bound invalidation: both
    // paths clear AG Grid, the mirrored store, and the local header state.
    useEffect(() => {
      window.addEventListener("observe-reset-selection", clearSelection);
      return () =>
        window.removeEventListener("observe-reset-selection", clearSelection);
    }, [clearSelection]);

    // Grid Options
    const defaultColDef = useMemo(
      () => ({
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderFilterButton: true,
        suppressHeaderContextMenu: true,
        sortable: false,
        minWidth: 200,
        flex: 1,
        cellStyle: {
          padding: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
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
    // Viewers can browse spans but not edit tags — gate the cell affordance.
    const canEditTags = Boolean(
      RolePermission.OBSERVABILITY[PERMISSIONS.CREATE_EDIT_PROJECT]?.[role],
    );
    // Tells cell renderers (e.g. TagsCell) they are on the span grid (so tag
    // edits target the span) and whether the role may edit.
    const gridContext = useMemo(
      () => ({ entityType: "span", canEditTags }),
      [canEditTags],
    );

    const { columnDefs } = useMemo(() => {
      // If no columns yet → return initial columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: SPAN_DEFAULT_COLUMNS,
          bottomRow: [],
        };
      }

      // If columns are populated → process normally
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
      const annotationColumns = generateAnnotationColumnsForTracing(
        grouping["Annotation Metrics"] || [],
        showMetricsIds,
      );
      delete grouping["Annotation Metrics"];
      const columnDefsResult = Object.entries(grouping).flatMap(
        ([group, cols]) => {
          if (!AllowedGroups.includes(group) && cols.length === 1) {
            const c = cols[0];
            bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
            const colDef = getSpanListColumnDefs(c);
            // Custom col: flat, but keep its width/style.
            if (c?.groupBy === "Custom Columns") {
              return {
                ...colDef,
                minWidth: 200,
                flex: 1,
                cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
              };
            }
            return colDef;
          }
          // marryChildren + groupId keep the group movable across rebuilds.
          return {
            headerName: group,
            groupId: group,
            marryChildren: true,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              const colDef = getSpanListColumnDefs(c);
              return {
                ...colDef,
                minWidth: 200,
                flex: 1,
                cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
              };
            }),
          };
        },
      );
      if (annotationColumns?.length > 0) {
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

    const dataSource = useMemo(
      () => {
        inFlightPageLoads.current.clear();
        cursorPagination.current.reset();
        return {
          getRows: async (params) => {
            if (!enabled) {
              // Disabled/unresolved is not an exact empty response. Reporting
              // success here makes AG Grid publish "No spans" without ever
              // calling the list API.
              withLiveGridApi(params.api, () => params.fail?.());
              return;
            }
            let pageNumber = 0;
            let firstPageRequestId = null;
            let pageLoadRequestId = null;
            let pageLoadSucceeded = false;
            let pageLoadRowCount = 0;
            let requestGeneration = null;
            let continuationPending = false;
            try {
              setLoading(true);
              const { request } = params;
              requestGeneration = cursorPagination.current.generation();

              const requestPageSize = request.endRow - request.startRow;
              pageNumber = Math.floor(request.startRow / requestPageSize);
              pageLoadRequestId = beginPageLoad(pageNumber);
              if (pageNumber === 0) {
                handoffToFirstPageRequest(filterRequestKey);
                firstPageRequestId = ++firstPageRequestRef.current;
                const preserveExistingRows =
                  preserveRowsDuringNextRefreshRef.current;
                preserveRowsDuringNextRefreshRef.current = false;
                if (!preserveExistingRows) setGridLoading(true);
                readStateRef.current = "complete";
                setReadState("complete");
                setContinuationNotice(null);
              }

              const buildParams = (page) =>
                cursorPagination.current.requestParams(page, {
                  // Omit project_id when null — backend treats absent
                  // project_id as org-scoped (used by user-detail page).
                  ...(observeId ? { project_id: observeId } : {}),
                  page_size: requestPageSize,
                  filters: JSON.stringify(
                    toBackendFilters([
                      ...filters,
                      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                      ...(extraFilters || EMPTY_EXTRA_FILTERS),
                      ...(metricFilters || []),
                    ]),
                  ),
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
                      loadSpanObservePage(buildParams(pageNumber), signal),
                    rowsFromResponse: (response) =>
                      response.data.table.map(boundObserveListRow),
                    metadataFromResponse: (response) => response.data.metadata,
                    compactResponse: compactObserveListResponse,
                    rowIdentity: getSpanPhysicalRowId,
                    isCurrent: () =>
                      cursorPagination.current.isCurrent(requestGeneration),
                    nextResponse: (_cursor, signal) =>
                      loadSpanObservePage(buildParams(pageNumber), signal),
                  }),
              });
              if (!isGridApiLive(params.api)) return;
              if (!cursorPagination.current.isCurrent(requestGeneration)) {
                // A newer filter/range owns the grid now. Do not let this stale
                // response replace its loading state with an empty overlay.
                params.fail();
                return;
              }

              const results = exactPage.response;
              const res = results.data;
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
                continuationPending = true;
                return;
              }
              const nextReadState = getQueryReadState(results.data);
              const visibleListReadState =
                rows.length > 0 || nextReadState === "sampled"
                  ? "complete"
                  : nextReadState;
              if (pageNumber === 0 || visibleListReadState !== "complete") {
                readStateRef.current = visibleListReadState;
                setReadState(visibleListReadState);
              }
              const newCols = normalizeConfigKeys(res?.config);

              // Use ref to get latest columns for comparison without triggering dataSource recreation
              // Compare only non-custom columns to avoid unnecessary re-renders
              if (newCols) {
                // The response config is authoritative and has not had saved-
                // view state applied. Capture it even on a cold saved-view load.
                const canonical = getCanonicalColumnSnapshot(newCols);
                if (canonicalOrderRef)
                  canonicalOrderRef.current = canonical.order;
                if (canonicalColumnsRef)
                  canonicalColumnsRef.current = canonical.columns;
                const firstAuthoritativeConfig =
                  authoritativeConfigProjectRef.current !== observeId;
                authoritativeConfigProjectRef.current = observeId;
                const currentColumns = columnsRef.current || [];
                const pending = pendingCustomColumnsRef?.current || [];
                // Diff by ID set — order isn't a schema change (TH-4996).
                const newIds = new Set(newCols.map((c) => c.id));
                // A persisted custom may now be a first-class API field. Count
                // that id as represented so the collision alias doesn't make
                // every subsequent page look like a schema change.
                const currentIdSet = new Set(
                  currentColumns
                    .filter(
                      (column) =>
                        column.groupBy !== "Custom Columns" ||
                        newIds.has(column.id),
                    )
                    .map((column) => column.id),
                );
                const idSetChanged =
                  newIds.size !== currentIdSet.size ||
                  [...newIds].some((id) => !currentIdSet.has(id));
                const hasPending = pending.length > 0;
                if (idSetChanged || hasPending || firstAuthoritativeConfig) {
                  if (pending.length > 0 && pendingCustomColumnsRef) {
                    pendingCustomColumnsRef.current = [];
                  }
                  setColumns(
                    mergeColumnsWithAuthoritativeConfig(
                      currentColumns,
                      newCols,
                      pending,
                    ),
                  );
                }
              }

              const totalState = getListTotalState(metadata);
              params.api.totalRowCount = totalState.totalRowCount;
              params.api.totalRowCountLowerBound =
                totalState.totalRowCountLowerBound;
              params.api.totalRowCountIsLowerBound =
                totalState.totalRowCountIsLowerBound;
              useSpanGridStore.setState(totalState);

              const isLastPage = exactPage.isLastPage;
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
              if (isListCursorContinuationLimitError(error)) {
                // Preserve the exact checkpoint and current rows. A deliberate
                // refresh may continue; do not publish a false empty page or
                // surface this bounded pause as a query error.
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
              readStateRef.current = "error";
              setReadState("error");
              failServerSideGridRead(params);
            } finally {
              finishPageLoad(pageLoadRequestId, {
                succeeded: pageLoadSucceeded,
                rowCount: pageLoadRowCount,
              });
              if (
                !continuationPending &&
                firstPageRequestId !== null &&
                firstPageRequestId === firstPageRequestRef.current
              ) {
                // The newest started page-zero request owns the loading state.
                // Cursor generations guard row publication above, but they must
                // not keep the overlay alive when a transition invalidates this
                // request before AG Grid starts its replacement. A replacement
                // getRows call increments the id and re-enters loading.
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
      [
        // Keep an in-flight semantic request alive when the parent rebuilds
        // equivalent filter arrays. Resetting the datasource in that case
        // invalidates the cursor generation and leaves the grid loading after
        // the completed page has already published its rows/empty result.
        filterRequestKey,
        beginPageLoad,
        finishPageLoad,
        handoffToFirstPageRequest,
        publishPage,
        setLoading,
      ],
    );

    // Propagate drag-reorder to parent so the View columns dropdown stays in sync.
    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;
        // User drags only; programmatic moves would feed back into setColumns.
        if (params.source !== "uiColumnMoved") return;
        const newOrder = params.api
          .getColumnState()
          .map((s) => s.colId)
          .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);
        const byId = new Map((columns || []).map((c) => [c.id, c]));
        const reordered = newOrder.map((id) => byId.get(id)).filter(Boolean);
        const matched = new Set(newOrder);
        const unmatched = (columns || []).filter((c) => !matched.has(c.id));
        const next = [...reordered, ...unmatched];
        const changed =
          next.length !== (columns || []).length ||
          next.some((c, i) => c.id !== columns[i]?.id);
        if (changed) setColumns(next);
      },
      [columns, setColumns],
    );

    const onSelectionChanged = useCallback((params) => {
      if (!isGridApiLive(params.api)) return;
      // Trust server-side selection state verbatim — [] is valid when
      // selectAll is true (no deselections). See TraceGrid for details.
      const isServerSide =
        typeof params.api.getServerSideSelectionState === "function";
      const ssState = isServerSide
        ? params.api.getServerSideSelectionState() || {}
        : {};
      const nodes = params.api.getSelectedNodes?.() || [];
      const idsFromNodes = nodes
        .map((n) => getSpanPhysicalRowId(n.data))
        .filter(Boolean);
      const toggled = isServerSide ? ssState.toggledNodes || [] : idsFromNodes;
      useSpanGridStore.setState({
        toggledNodes: toggled,
        selectAll: !!ssState.selectAll,
      });
    }, []);

    const handleCellClick = useCallback(
      (event) => {
        if (!event?.node?.id) {
          //discard clicks on empty rows
          return;
        }
        if (event?.column?.colId === "status") {
          return;
        }
        if (RENDERER_CONFIG.tagColumns.includes(event?.column?.getColId())) {
          return;
        }
        if (
          event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
        ) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          // Belt-and-suspenders: sync store directly (see TraceGrid note).
          setTimeout(() => {
            if (!isGridApiLive(event.api)) return;
            const isServerSide =
              typeof event.api.getServerSideSelectionState === "function";
            const ssState = isServerSide
              ? event.api.getServerSideSelectionState() || {}
              : {};
            const nodes = event.api.getSelectedNodes?.() || [];
            const idsFromNodes = nodes
              .map((n) => getSpanPhysicalRowId(n.data))
              .filter(Boolean);
            const toggled = isServerSide
              ? ssState.toggledNodes || []
              : idsFromNodes;
            useSpanGridStore.setState({
              toggledNodes: toggled,
              selectAll: !!ssState.selectAll,
            });
          }, 0);
          return;
        }

        const traceId = event?.data?.trace_id;
        const spanId = event?.data?.span_id;
        if (!traceId || !spanId) {
          return;
        }
        setSpanDetailDrawerOpen({
          trace_id: traceId,
          span_id: spanId,
          filters: filters,
          fromSpansView: true,
        });

        trackEvent(Events.observeSpanidClicked);
      },
      [filters, setSpanDetailDrawerOpen],
    );

    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);
    return (
      <Box
        ref={gridElementRef}
        sx={{
          height: "calc(100vh - 270px)",
          display: "flex",
          flexDirection: "column",
        }}
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
          key={`span-grid-${pageSize}`}
          style={{ flex: 1, minHeight: 0 }}
          className={`${cellHeight && cellHeight !== "Short" ? "cell-wrap " : ""}clean-data-table${continuationNotice ? " ag-grid-cursor-paused" : ""}`}
          // rowSelection={{ mode: "multiRow" }}
          rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
          theme={agTheme}
          ref={gridRef}
          columnDefs={columnDefs}
          onColumnMoved={onColumnMoved}
          defaultColDef={defaultColDef}
          context={gridContext}
          rowSelection={{ mode: "multiRow", enableClickSelection: false }}
          pagination={true}
          paginationPageSize={pageSize}
          paginationPageSizeSelector={false}
          suppressPaginationPanel={true}
          cacheBlockSize={pageSize}
          maxBlocksInCache={OBSERVE_GRID_MAX_BLOCKS_IN_CACHE}
          maxConcurrentDatasourceRequests={OBSERVE_GRID_MAX_CONCURRENT_REQUESTS}
          rowBuffer={5}
          rowModelType="serverSide"
          tooltipShowDelay={0}
          tooltipHideDelay={2000}
          tooltipInteraction={true}
          serverSideDatasource={dataSource}
          // The footer owns explicit page-transition feedback. AG Grid must be
          // free to paint the target rows before that transition can settle.
          loading={gridLoading || transitionLoading}
          suppressServerSideFullWidthLoadingRow={true}
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
                    {getQueryReadMessage(readStateRef.current) ||
                      "No spans found"}
                  </Typography>,
                )
          }
          onCellClicked={handleCellClick}
          onSelectionChanged={onSelectionChanged}
          // onGridReady={(params) => {
          //   timeoutRef.current = setTimeout(() => {
          //     params.api.sizeColumnsToFit([
          //       "latencyMs", "latency", "totalCost", "status", "totalCost", "cost", "totalTokens"
          //     ]);
          //   }, 200);
          // }}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
              return;
            }
            if (!isGridApiLive(event.api)) return;

            if (selectedAll) {
              event.api.deselectAll();
              setSelectedAll(false);
            } else {
              event.api.selectAll();
              setSelectedAll(true);
            }
          }}
          // suppressColumnMoveAnimation={true}
          // suppressColumnVirtualisation={true}
          statusBar={statusBar}
          blockLoadDebounceMillis={300}
          getRowId={(d) => getSpanPhysicalRowId(d?.data)}
        />
        <CursorGridPagination
          disabled={
            !enabled ||
            gridLoading ||
            transitionLoading ||
            isPageLoading ||
            Boolean(continuationNotice)
          }
          loading={isPageLoading}
          page={page}
          pageCount={pageCount}
          pageSize={pageSize}
          onPageChange={goToPage}
          onPageSizeChange={changePageSize}
        />
        <LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />
        <NumberQuickFilterPopover
          open={Boolean(openQuickFilter)}
          filterData={openQuickFilter}
          onClose={() => setOpenQuickFilter(null)}
          setFilters={setFilters}
        />
      </Box>
    );
  },
);

SpanGrid.displayName = "SpanGrid";

SpanGrid.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  filters: PropTypes.array,
  extraFilters: PropTypes.array,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.func,
  setLoading: PropTypes.func,
  setPageMap: PropTypes.func,
  compareType: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
  cellHeight: PropTypes.string,
  metricFilters: PropTypes.array,
  enabled: PropTypes.bool,
};

export default SpanGrid;
