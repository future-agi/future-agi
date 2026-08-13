import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import { useQueryClient } from "@tanstack/react-query";
import { useAgTheme } from "src/hooks/use-ag-theme";
import {
  Box,
  Button,
  MenuItem,
  Pagination,
  PaginationItem,
  Select,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import {
  getCallLogsColumnDefs,
  useCallLogs,
  prefetchCallLogs,
} from "../helper";
import Iconify from "src/components/iconify";
import { useAgentDetailsStore } from "../store/agentDetailsStore";
import TestDetailSideDrawer from "src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer";
import {
  resetState,
  useTestDetailSideDrawerStoreShallow,
} from "src/sections/test-detail/states";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { useShallowToggleAnnotationsStore } from "../store";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import { APP_CONSTANTS } from "src/utils/constants";
import {
  getQueryReadMessage,
  getQueryReadState,
} from "src/utils/queryReadState";
import {
  LIST_CURSOR_CONTINUATION_NOTICE,
  createListCursorPagination,
  isListCursorContinuationLimitError,
  isListCursorProtocolError,
} from "src/sections/projects/LLMTracing/listCursorPagination";

const CELL_HEIGHT_MAP = { Short: 40, Medium: 52, Large: 68, "Extra Large": 88 };

// Padding matches CallLogsCellRenderer.jsx so custom-col cells align with
// the rest of the row.
const CustomColCellRenderer = (params) => {
  const v = params?.value;
  const display = v == null || v === "" ? "-" : v;
  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.5,
        display: "flex",
        alignItems: "center",
        height: "100%",
      }}
    >
      <Typography variant="body2" sx={{ fontSize: 13 }} noWrap>
        {String(display)}
      </Typography>
    </Box>
  );
};

const CustomColLoadingSkeleton = () => (
  <Skeleton
    variant="rectangular"
    width="80%"
    height={15}
    sx={{ mx: 1, borderRadius: 0.5 }}
  />
);

const TERMINAL_CALL_STATUSES = new Set([
  "completed",
  "dropped",
  "ended",
  "error",
  "failed",
  "not-connected",
  "ok",
]);

const isSelectableForAnnotation = (row) => {
  const status = String(row?.status || "").toLowerCase();
  return TERMINAL_CALL_STATUSES.has(status);
};

const CallLogsGrid = React.forwardRef(function CallLogsGrid(
  {
    id,
    params = {},
    onRowClicked = (_params, page, pageLimit) => {},
    module = "simulate",
    onConfigLoaded = () => {},
    enabled = true,
    onSelectionChanged,
    // Richer selection callback used by LLMTracingView's simulator branch
    // to decide when to show the "select all matching filter" banner.
    onSelectionMeta,
    cellHeight = "Short",
    columnVisibility,
    onColumnsChange,
    hideDrawer = false,
    showErrors = false,
  },
  forwardedRef,
) {
  const agTheme = useAgTheme();
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(15);
  const [totalPages, setTotalPages] = useState(1);
  const [cursorTransportRevision, advanceCursorTransport] = useState(0);
  const cursorPagination = useRef(
    createListCursorPagination({ pageParam: "page", pageOffset: 1 }),
  );
  const { selectedVersion } = useAgentDetailsStore();
  const cursorQuerySignature = JSON.stringify({
    id,
    module,
    selectedVersion,
    params,
  });
  // LLMTracingView builds request params inline. Keep the latest equivalent
  // object available to effects without making its reference an effect
  // dependency: otherwise any unrelated parent render re-runs a stale
  // next-page prefetch for the same semantic query.
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const [lastCursorQuerySignature, setLastCursorQuerySignature] =
    useState(cursorQuerySignature);
  const [callLogsColumnDefs, setCallLogsColumnDefs] = useState(null);
  const previousConfigRef = useRef({
    configLength: undefined,
    showMetricsIds: undefined,
    isLoading: undefined,
  });
  const { reset: resetToggleAnnotationsStore } =
    useShallowToggleAnnotationsStore((state) => ({
      reset: state.reset,
    }));

  // Highlight the row whose detail drawer is open (mirrors TraceGrid).
  const { testDetailDrawerOpen } = useTestDetailSideDrawerStoreShallow(
    (state) => ({
      testDetailDrawerOpen: state.testDetailDrawerOpen,
    }),
  );
  const activeCallId =
    testDetailDrawerOpen?.id || testDetailDrawerOpen?.trace_id || null;
  const getRowStyle = useCallback(
    (params) => {
      const rowId = params.data?.id || params.data?.trace_id;
      if (rowId && activeCallId && rowId === activeCallId) {
        return {
          backgroundColor: "rgba(120, 87, 252, 0.08)",
          cursor: "pointer",
        };
      }
      return { cursor: "pointer" };
    },
    [activeCallId],
  );
  // Reset the opaque chain synchronously whenever any query-shaping input
  // changes. A cursor is signed against the complete normalized request, not
  // only the visible filter array.
  if (lastCursorQuerySignature !== cursorQuerySignature) {
    cursorPagination.current.reset();
    setLastCursorQuerySignature(cursorQuerySignature);
    setPage(1);
  }

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      sortable: false,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      minWidth: 180,
      cellStyle: {
        padding: "0px",
        display: "flex",
        alignItems: "center",
      },
    }),
    [],
  );

  useEffect(() => {
    return () => resetState();
  }, []);

  const { showMetricsIds } = useShallowToggleAnnotationsStore((state) => ({
    showMetricsIds: state.showMetricsIds,
  }));
  const gridRef = useRef(null);
  useImperativeHandle(
    forwardedRef,
    () => ({
      deselectAll: () => gridRef.current?.api?.deselectAll(),
      // Read api lazily so callers always hit the live grid instance,
      // not a null captured at forwardRef-mount time.
      get api() {
        return gridRef.current?.api;
      },
    }),
    [],
  );
  const bufferedPage =
    module === "project"
      ? cursorPagination.current.bufferedVisiblePage(page - 1)
      : null;
  const paginationRequest =
    module === "project"
      ? {
          generation: cursorPagination.current.generation(),
          // Terminal overflow is already buffered by the preceding visible
          // page and intentionally has no continuation cursor.
          params:
            bufferedPage?.metadata?.has_more === false
              ? undefined
              : cursorPagination.current.requestParams(page - 1, {
                  page_size: pageLimit,
                }),
        }
      : { generation: null, params: undefined };
  const { data, isLoading, error, queryKey } = useCallLogs({
    module,
    id: id,
    version: selectedVersion,
    page,
    pageLimit,
    params,
    paginationParams: paginationRequest.params,
    paginationRevision: cursorTransportRevision,
    cursorPagination:
      module === "project" ? cursorPagination.current : undefined,
    paginationGeneration: paginationRequest.generation,
    enabled,
  });
  const exactPage = data?.__exactPage || data?.result?.__exactPage || null;
  const hasBufferedSameGenerationError =
    module === "project" &&
    Boolean(error) &&
    cursorPagination.current.isCurrent(paginationRequest.generation) &&
    Boolean(bufferedPage) &&
    !isListCursorProtocolError(error);
  const cursorContinuationPaused =
    module === "project" &&
    (exactPage?.pending === true ||
      isListCursorContinuationLimitError(error) ||
      hasBufferedSameGenerationError);
  const readState = useMemo(
    () =>
      getQueryReadState(data, {
        isError: Boolean(error) && !cursorContinuationPaused,
      }),
    [cursorContinuationPaused, data, error],
  );
  const responseRows =
    isListCursorContinuationLimitError(error) || hasBufferedSameGenerationError
      ? bufferedPage?.rows || []
      : Array.isArray(data?.results)
        ? data.results
        : [];
  const hasCursorContinuation =
    module === "project" &&
    (exactPage
      ? exactPage.pending === true || exactPage.isLastPage === false
      : data?.has_more === true &&
        typeof data?.next_cursor === "string" &&
        data.next_cursor.length > 0);
  const hasCursorContract =
    module === "project" &&
    typeof data?.has_more === "boolean" &&
    Object.prototype.hasOwnProperty.call(data || {}, "next_cursor");
  const readMessage =
    cursorContinuationPaused ||
    responseRows.length > 0 ||
    readState === "sampled" ||
    hasCursorContinuation
      ? null
      : getQueryReadMessage(readState);
  const isCompleteRead = readState === "complete";
  const isUsableListRead =
    !error &&
    (isCompleteRead || responseRows.length > 0 || hasCursorContinuation);

  useEffect(() => {
    if (
      module !== "project" ||
      isLoading ||
      error ||
      !data ||
      !cursorPagination.current.isCurrent(paginationRequest.generation)
    ) {
      return;
    }
    try {
      if (exactPage) {
        return;
      }
      if (responseRows.length === 0 && hasCursorContinuation) {
        cursorPagination.current.recordEmptyContinuation(page - 1, data);
        // Keep the same visible pagination page. The project query key includes
        // the advanced opaque cursor, so this rerender fetches the next bounded
        // transport prefix without flashing an empty page to the user.
        advanceCursorTransport((revision) => revision + 1);
        return;
      }
      cursorPagination.current.recordResponse(page - 1, data);
    } catch (cursorError) {
      if (
        cursorPagination.current.canRecoverFromContinuationError(
          page - 1,
          cursorError,
        )
      ) {
        cursorPagination.current.disableCursor();
        setPage(1);
      }
    }
  }, [
    data,
    error,
    exactPage,
    hasCursorContinuation,
    isLoading,
    module,
    page,
    paginationRequest.generation,
    responseRows.length,
  ]);

  const continueCursorSearch = useCallback(() => {
    if (!cursorContinuationPaused) return;
    advanceCursorTransport((revision) => revision + 1);
  }, [cursorContinuationPaused]);

  useEffect(() => {
    if (!isLoading) {
      const reportedPages = Number(data?.total_pages) || 1;
      const continuationFloor = hasCursorContinuation ? page + 1 : page;
      setTotalPages(
        isUsableListRead
          ? Math.max(
              1,
              continuationFloor,
              hasCursorContract ? page : reportedPages,
            )
          : 1,
      );
    }
  }, [
    data?.has_more,
    data?.total_pages,
    hasCursorContract,
    hasCursorContinuation,
    isLoading,
    isUsableListRead,
    page,
  ]);

  const rows = useMemo(() => {
    if (isLoading) {
      return Array.from({ length: 10 }, (_, index) => ({
        id: index,
        call_summary: "",
        customer_number: "",
        duration_seconds: "",
        overall_score: "",
        status: "",
      }));
    }
    return responseRows;
  }, [isLoading, responseRows]);

  // Pass full column list to parent (base + eval/annotation) for DisplayPanel.
  // Use a ref to avoid re-firing when callLogsColumnDefs reference changes
  // but content is the same (prevents render loops).
  const lastReportedDefsLenRef = useRef(null);
  useEffect(() => {
    if (
      callLogsColumnDefs?.length > 0 &&
      callLogsColumnDefs.length !== lastReportedDefsLenRef.current
    ) {
      lastReportedDefsLenRef.current = callLogsColumnDefs.length;
      const colConfig = callLogsColumnDefs
        .filter((c) => c.field)
        .map((c) => ({
          id: c.field,
          field: c.field,
          name: c.headerName || c.field,
          isVisible: !c.hide,
          groupBy: c.field.match(/^[0-9a-f-]{36}/)
            ? "Evaluation Metrics"
            : "Call Columns",
        }));
      onConfigLoaded(colConfig);
    }
  }, [callLogsColumnDefs, onConfigLoaded]);

  // Prefetch next page so pagination feels instant
  useEffect(() => {
    if (
      isUsableListRead &&
      responseRows.length > 0 &&
      page < totalPages &&
      (!exactPage || exactPage.canPrefetch)
    ) {
      const nextPaginationParams =
        module === "project"
          ? cursorPagination.current.requestParams(page, {
              page_size: pageLimit,
            })
          : undefined;
      prefetchCallLogs(queryClient, {
        module,
        id,
        version: selectedVersion,
        page: page + 1,
        pageLimit,
        params: paramsRef.current,
        paginationParams: nextPaginationParams,
        paginationRevision: cursorTransportRevision,
        cursorPagination:
          module === "project" ? cursorPagination.current : undefined,
        paginationGeneration:
          module === "project"
            ? cursorPagination.current.generation()
            : undefined,
      });
    }
  }, [
    data,
    page,
    totalPages,
    queryClient,
    module,
    id,
    selectedVersion,
    pageLimit,
    cursorQuerySignature,
    exactPage,
    cursorTransportRevision,
    isUsableListRead,
    responseRows.length,
  ]);

  const configLength = data?.config?.length;
  if (
    previousConfigRef.current.configLength !== configLength ||
    previousConfigRef.current.showMetricsIds !== showMetricsIds ||
    previousConfigRef.current.isLoading !== isLoading
  ) {
    previousConfigRef.current = { configLength, showMetricsIds, isLoading };
    setCallLogsColumnDefs(
      getCallLogsColumnDefs(
        rows,
        isLoading,
        null,
        module,
        data?.config,
        showMetricsIds,
      ),
    );
  }

  // Apply external column visibility + add custom columns from parent
  const effectiveDefs = useMemo(() => {
    if (!callLogsColumnDefs) return callLogsColumnDefs;

    const visMap = {};
    const orderIndex = new Map();
    const customCols = [];
    (columnVisibility || []).forEach((c, i) => {
      if (c.field) {
        visMap[c.field] = c.isVisible !== false;
        orderIndex.set(c.field, i);
      }
      if (c.groupBy === "Custom Columns") {
        customCols.push(c);
        // colId key so customs sort into their own store position.
        orderIndex.set(c.id, i);
      }
    });

    const updated = callLogsColumnDefs.map((col) => ({
      ...col,
      ...(col.field && col.field in visMap && { hide: !visMap[col.field] }),
    }));

    // Add column defs for custom columns not already in the grid
    const existingFields = new Set(callLogsColumnDefs.map((c) => c.field));
    const newCustomDefs = customCols
      .filter((c) => !existingFields.has(c.id))
      .map((c) => ({
        headerName: c.name,
        // colId (not field) so AG Grid doesn't deep-resolve the dotted path
        // — list_voice_calls returns flat rows; the valueGetter below handles
        // the resolution.
        colId: c.id,
        flex: 0,
        minWidth: 120,
        hide: c.isVisible === false,
        cellRenderer: isLoading
          ? CustomColLoadingSkeleton
          : CustomColCellRenderer,
        valueGetter: (params) => {
          if (!params.data) return null;
          let value = params.data[c.id];
          if (value === undefined && c.id.includes(".")) {
            value = c.id
              .split(".")
              .reduce((obj, key) => obj?.[key], params.data);
          }
          // /eval-attributes serves Vapi attribute paths with namespace
          // prefixes (call.*, vapi.*) but /list_voice_calls returns them
          // as flat keys. Whitelisted — a generic "drop leading segments"
          // would false-positive on paths like phone_number.id → row.id.
          const VOICE_FLAT_NAMESPACE_PREFIXES = ["call.", "vapi."];
          if (value === undefined) {
            const matchedPrefix = VOICE_FLAT_NAMESPACE_PREFIXES.find((p) =>
              c.id.startsWith(p),
            );
            if (matchedPrefix) {
              value = params.data[c.id.slice(matchedPrefix.length)];
            }
          }
          if (value === undefined || value === null) return null;
          if (Array.isArray(value) || typeof value === "object") {
            return JSON.stringify(value);
          }
          return String(value);
        },
      }));

    // Sort base + custom together so customs sit flat at their own positions.
    const combined = [...updated, ...newCustomDefs];
    combined.sort((a, b) => {
      const ai = orderIndex.get(a?.field ?? a?.colId) ?? Infinity;
      const bi = orderIndex.get(b?.field ?? b?.colId) ?? Infinity;
      return ai - bi;
    });
    return combined;
  }, [callLogsColumnDefs, columnVisibility, isLoading]);
  useEffect(() => {
    return () => {
      resetToggleAnnotationsStore();
    };
  }, []);

  // Propagate reorder to parent so the View columns dropdown stays in sync.
  const onColumnMoved = useCallback(
    (params) => {
      if (
        !params?.finished ||
        !params?.api ||
        typeof onColumnsChange !== "function"
      )
        return;
      // User drags only — a programmatic move would rebuild the shared trace
      // `columns` from this grid's voice-only state and corrupt it.
      if (params.source !== "uiColumnMoved") return;
      const newOrder = (params?.api?.getColumnState() ?? [])
        .map((s) => s.colId)
        .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);

      const cols = columnVisibility || [];
      const byColId = new Map(cols.map((c) => [c.field || c.id, c]));
      const reordered = newOrder.map((id) => byColId.get(id)).filter(Boolean);
      const matched = new Set(newOrder);
      const unmatched = cols.filter((c) => !matched.has(c.field || c.id));
      const next = [...reordered, ...unmatched];

      const sameOrder =
        next.length === cols.length &&
        next.every(
          (c, i) => (c?.field || c?.id) === (cols[i]?.field || cols[i]?.id),
        );
      if (!sameOrder) onColumnsChange(next);
    },
    [columnVisibility, onColumnsChange],
  );

  return (
    <Box sx={{ height: "78vh", display: "flex" }}>
      <Box
        className="ag-theme-alpine"
        sx={{
          flex: 1,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          "& .ag-cell-wrapper": {
            flex: "1 !important",
            height: "100%",
          },
          "& .ag-cell-wrapper > span": {
            height: "100%",
          },
        }}
      >
        {cursorContinuationPaused && (
          <Box
            role="status"
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
              px: 1.5,
              py: 0.75,
              color: "text.secondary",
              bgcolor: "action.hover",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography variant="caption">
              {LIST_CURSOR_CONTINUATION_NOTICE}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              onClick={continueCursorSearch}
            >
              Continue search
            </Button>
          </Box>
        )}
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
        {/* Grid fills available space */}
        <Box sx={{ flex: 1, minHeight: 0 }}>
          <AgGridReact
            ref={gridRef}
            className="clean-data-table"
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
            rowHeight={CELL_HEIGHT_MAP[cellHeight] || 40}
            columnDefs={effectiveDefs}
            onColumnMoved={onColumnMoved}
            defaultColDef={defaultColDef}
            rowData={rows}
            loading={false}
            suppressServerSideFullWidthLoadingRow={true}
            rowSelection={
              onSelectionChanged
                ? { mode: "multiRow", enableClickSelection: false }
                : undefined
            }
            selectionColumnDef={
              onSelectionChanged
                ? { pinned: true, lockPinned: true }
                : undefined
            }
            pagination={false}
            noRowsOverlayComponent={() =>
              cursorContinuationPaused
                ? null
                : NoRowsOverlay(
                    <Typography
                      sx={{
                        fontSize: 14,
                        fontWeight: 400,
                        color: "text.secondary",
                      }}
                    >
                      {readMessage ||
                        (showErrors ? "No error found" : "No calls found")}
                    </Typography>,
                  )
            }
            getRowStyle={getRowStyle}
            onRowClicked={(params) => {
              onRowClicked(params, page, pageLimit);
            }}
            onSelectionChanged={
              onSelectionChanged
                ? (event) => {
                    const selectedRows = event.api.getSelectedRows();
                    const traceIds = selectedRows
                      .map((row) => row.trace_id)
                      .filter(Boolean);
                    onSelectionChanged(traceIds);
                    if (onSelectionMeta) {
                      const currentPageSize = rows?.length || 0;
                      // Keep the backend's explicit lower-bound marker with
                      // the count; callers must not present it as exact.
                      const totalMatching =
                        typeof data?.count === "number" ? data.count : null;
                      const unavailableSelectedCount = selectedRows.filter(
                        (row) =>
                          row?.trace_id && !isSelectableForAnnotation(row),
                      ).length;
                      onSelectionMeta({
                        traceIds,
                        selectedCount: traceIds.length,
                        unavailableSelectedCount,
                        isAllOnPageSelected:
                          currentPageSize > 0 &&
                          selectedRows.length === currentPageSize,
                        currentPageSize,
                        totalPages,
                        pageLimit,
                        totalMatching,
                        totalMatchingIsLowerBound:
                          data?.count_is_lower_bound === true,
                      });
                    }
                  }
                : undefined
            }
          />
        </Box>
        <ShowComponent condition={!hideDrawer && module === "project"}>
          <TestDetailSideDrawer
            origin="project"
            drawerQueryKey={queryKey.slice(0, -1)}
          />
        </ShowComponent>
        <ShowComponent condition={!hideDrawer && module === "simulate"}>
          <TestDetailSideDrawer
            drawerQueryKey={queryKey.slice(0, -1)}
            origin={"agent-definition"}
          />
        </ShowComponent>

        {/* Footer controls */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ p: 1, borderTop: "1px solid var(--border-default)" }}
        >
          <Stack gap={1} direction="row" alignItems="center">
            <Typography
              typography="s2"
              color="text.primary"
              fontWeight="fontWeightRegular"
            >
              Results per page
            </Typography>

            <Select
              size="small"
              id="page-size-select"
              value={pageLimit}
              onChange={(e) => {
                cursorPagination.current.reset();
                setPage(1);
                setPageLimit(Number(e.target.value));
              }}
              sx={{ height: 36, bgcolor: "background.paper" }}
            >
              {[10, 15, 25, 50].map((size) => (
                <MenuItem key={size} value={size}>
                  {size}
                </MenuItem>
              ))}
            </Select>
          </Stack>

          <Pagination
            count={isUsableListRead ? totalPages : 1}
            variant="outlined"
            shape="rounded"
            page={isUsableListRead ? page : 1}
            color="primary"
            disabled={!isUsableListRead}
            onChange={(e, value) => {
              setPage(value);
            }}
            renderItem={(item) => (
              <PaginationItem
                {...item}
                sx={{
                  borderRadius: "4px",
                  bgcolor: "background.paper",
                }}
                slots={{
                  previous: () => (
                    <Box display={"flex"} alignItems={"center"} gap={0.5}>
                      <Iconify
                        icon="octicon:chevron-left-24"
                        width={18}
                        height={18}
                        sx={{
                          path: { strokeWidth: 1.5 },
                        }}
                      />{" "}
                      Back
                    </Box>
                  ),
                  next: () => (
                    <Box display={"flex"} alignItems={"center"} gap={0.5}>
                      Next{" "}
                      <Iconify
                        icon="octicon:chevron-right-24"
                        width={18}
                        height={18}
                        sx={{
                          path: { strokeWidth: 1.5 },
                        }}
                      />
                    </Box>
                  ),
                }}
              />
            )}
          />
        </Stack>
      </Box>
    </Box>
  );
});

export default CallLogsGrid;

CallLogsGrid.propTypes = {
  id: PropTypes.string,
  module: PropTypes.string,
  params: PropTypes.object,
  onRowClicked: PropTypes.func,
  onConfigLoaded: PropTypes.func,
  enabled: PropTypes.bool,
  onSelectionChanged: PropTypes.func,
  onSelectionMeta: PropTypes.func,
  cellHeight: PropTypes.string,
  columnVisibility: PropTypes.array,
  onColumnsChange: PropTypes.func,
  hideDrawer: PropTypes.bool,
  showErrors: PropTypes.bool,
};
