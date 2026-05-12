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

const CELL_HEIGHT_MAP = { Short: 40, Medium: 52, Large: 68, "Extra Large": 88 };

// Inline cell renderer for custom columns. Standard CallLogs cells wrap
// content in a Box with `px: 1.5, py: 0.5` (CallLogsCellRenderer.jsx:262-274)
// — without matching that here the custom-col content rendered flush
// against the cell's left edge, breaking visual alignment with the rest of
// the row.
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

// Loading skeleton — same shape as the standard helper.jsx `LoadingSkeleton`,
// inlined so the custom-col defs aren't coupled to that internal helper.
const CustomColLoadingSkeleton = () => (
  <Skeleton
    variant="rectangular"
    width="80%"
    height={15}
    sx={{ mx: 1, borderRadius: 0.5 }}
  />
);

const CallLogsGrid = React.forwardRef(function CallLogsGrid(
  {
    id,
    params = {},
    onRowClicked = (_params, page, pageLimit) => {},
    module = "simulate",
    onConfigLoaded = () => {},
    enabled = true,
    onSelectionChanged,
    // Optional richer selection callback — fires alongside onSelectionChanged
    // with {traceIds, isAllOnPageSelected, currentPageSize, totalPages,
    // pageLimit}. Used by LLMTracingView's simulator branch to decide when
    // to show the "select all matching filter" banner (Phase 9 of the
    // annotation-queue-bulk-select revamp).
    onSelectionMeta,
    cellHeight = "Short",
    columnVisibility,
    hideDrawer = false,
    showErrors = false,
  },
  forwardedRef,
) {
  const agTheme = useAgTheme();
  const theme = useTheme();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(25);
  const [totalPages, setTotalPages] = useState(1);
  const [lastFilters, setLastFilters] = useState(params?.filters);
  const { selectedVersion } = useAgentDetailsStore();
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

  // Highlight the row whose detail drawer is currently open. Mirrors the
  // TraceGrid activeTraceId pattern so the experience matches the trace
  // detail drawer the user referenced.
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
  // Derived state: reset page synchronously when filters change (avoids extra API call)
  if (lastFilters !== params?.filters) {
    setLastFilters(params?.filters);
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
      suppressMultiSort: true,
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
  const { data, isLoading, queryKey } = useCallLogs({
    module,
    id: id,
    version: selectedVersion,
    page,
    pageLimit,
    params,
    enabled,
  });

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
    } else {
      setTotalPages(data?.total_pages || 1);
    }
    return data?.results || [];
  }, [data, isLoading]);

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
    if (data?.results?.length > 0 && page < totalPages) {
      prefetchCallLogs(queryClient, {
        module,
        id,
        version: selectedVersion,
        page: page + 1,
        pageLimit,
        params,
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
    params,
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
    const customCols = [];
    (columnVisibility || []).forEach((c) => {
      if (c.field) visMap[c.field] = c.isVisible !== false;
      if (c.groupBy === "Custom Columns") customCols.push(c);
    });

    // Toggle visibility on existing defs
    const updated = callLogsColumnDefs.map((col) => {
      if (col.field && col.field in visMap) {
        return { ...col, hide: !visMap[col.field] };
      }
      return col;
    });

    // Add column defs for custom columns not already in the grid
    const existingFields = new Set(callLogsColumnDefs.map((c) => c.field));
    const newCustomDefs = customCols
      .filter((c) => !existingFields.has(c.id))
      .map((c) => ({
        headerName: c.name,
        // Use colId rather than `field` for custom cols so AG Grid doesn't
        // try to deep-resolve the dotted path against the row object (which
        // for voice/list_voice_calls is FLAT — no nested `call` member). We
        // do the lookup ourselves in valueGetter below.
        colId: c.id,
        flex: 0,
        minWidth: 120,
        hide: c.isVisible === false,
        // While the first fetch is in-flight, render the same skeleton
        // bar the standard cols use (helper.jsx:408 LoadingSkeleton). After
        // load, swap to the styled renderer that gives the custom-col cell
        // the same px/py padding as the standard CallLogsCellRenderer.
        cellRenderer: isLoading ? CustomColLoadingSkeleton : CustomColCellRenderer,
        valueGetter: (params) => {
          if (!params.data) return null;
          // 1. Direct lookup (handles attributes whose stored id is the
          //    literal data key, e.g. "bot_wpm").
          let value = params.data[c.id];
          // 2. Dot-notation traversal (handles nested response shapes,
          //    matches TraceGrid/getCustomValueGetter).
          if (value === undefined && c.id.includes(".")) {
            value = c.id
              .split(".")
              .reduce((obj, key) => obj?.[key], params.data);
          }
          // 3. Voice-specific namespace strip: the /eval-attributes list
          //    serves Vapi-style attribute paths with explicit namespace
          //    prefixes (e.g. "call.bot_wpm", "vapi.call_id"), but the
          //    /list_voice_calls/ response returns those same values as
          //    top-level flat keys ("bot_wpm", "call_id"). Strip the prefix
          //    and try direct lookup.
          //
          //    Whitelist intentionally — only namespaces where the data is
          //    proven to live flat in the list response. A generic
          //    "drop leading segments" fallback would false-positive on
          //    paths like "phone_number.id" → row.id (the row primary key).
          //
          //    Attributes outside this whitelist (conversation.*,
          //    performance_metrics.*, workflow.*, gen_ai.*, etc.) are
          //    span-detail paths the list endpoint doesn't include — they
          //    correctly fall through to "-" until the backend expands the
          //    list response or removes them from the picker for voice.
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

    // Wrap customs under a "Custom Columns" group header so they render as
    // a grouped section at the end of the grid (parity with TraceGrid /
    // SpanGrid / SessionGrid). Without this they appeared as flat columns
    // alongside the standard ones, with no visual separation.
    if (newCustomDefs.length > 0) {
      return [
        ...updated,
        { headerName: "Custom Columns", children: newCustomDefs },
      ];
    }
    return updated;
  }, [callLogsColumnDefs, columnVisibility, isLoading]);
  useEffect(() => {
    return () => {
      resetToggleAnnotationsStore();
    };
  }, []);

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
            defaultColDef={defaultColDef}
            rowData={rows}
            suppressServerSideFullWidthLoadingRow={true}
            suppressRowClickSelection
            rowSelection={onSelectionChanged ? { mode: "multiRow" } : undefined}
            selectionColumnDef={
              onSelectionChanged
                ? { pinned: true, lockPinned: true }
                : undefined
            }
            pagination={false}
            serverSideInitialRowCount={5}
            noRowsOverlayComponent={() =>
              NoRowsOverlay(
                <Typography
                  sx={{
                    fontSize: 14,
                    fontWeight: 400,
                    color: "text.secondary",
                  }}
                >
                  {showErrors ? "No error found" : "No calls found"}
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
                      // `data.count` is the exact matching-row count from
                      // the backend (CH or PG), not `totalPages * pageLimit`
                      // which rounds up to a page multiple and overstates
                      // the banner by up to `pageLimit - 1` rows.
                      const totalMatching =
                        typeof data?.count === "number" ? data.count : null;
                      onSelectionMeta({
                        traceIds,
                        isAllOnPageSelected:
                          currentPageSize > 0 &&
                          selectedRows.length === currentPageSize,
                        currentPageSize,
                        totalPages,
                        pageLimit,
                        totalMatching,
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
                setPage(1);
                setPageLimit(Number(e.target.value));
              }}
              sx={{ height: 36, bgcolor: "background.paper" }}
            >
              {[10, 25, 50].map((size) => (
                <MenuItem key={size} value={size}>
                  {size}
                </MenuItem>
              ))}
            </Select>
          </Stack>

          <Pagination
            count={totalPages}
            variant="outlined"
            shape="rounded"
            page={page}
            color="primary"
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
  hideDrawer: PropTypes.bool,
  showErrors: PropTypes.bool,
};
