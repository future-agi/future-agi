import PropTypes from "prop-types";
import React, {
  useState,
  useCallback,
  useMemo,
  useRef,
  useEffect,
} from "react";
import { enqueueSnackbar } from "notistack";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Drawer,
  IconButton,
  InputAdornment,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import { useAddQueueItems } from "src/api/annotation-queues/annotation-queues";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AGGridCellDataType } from "src/utils/constant";
import { parseCellValue } from "src/utils/agUtils";
import CustomCellRender from "src/sections/common/DevelopCellRenderer/CustomCellRender";
import CustomDevelopDetailColumn from "src/sections/common/CustomDevelopDetailColumn";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import {
  DefaultFilter,
  validateFilter,
  transformFilter,
} from "src/sections/develop-detail/DataTab/DevelopFilters/common";
import DevelopFilterRow from "src/sections/develop-detail/DataTab/DevelopFilters/DevelopFilterRow";
import { getRandomId } from "src/utils/utils";
import { isEqual } from "lodash";
import "src/sections/develop-detail/DataTab/developDataGrid.css";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useTestRunsList } from "src/api/tests/testRuns";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import { objectCamelToSnake } from "src/utils/utils";
import {
  getTraceListColumnDefs,
  TRACE_DEFAULT_COLUMNS,
  generateObserveTraceFilterDefinition,
  generateSpanObserveFilterDefinition,
  SPAN_DEFAULT_COLUMNS,
} from "src/sections/projects/LLMTracing/common";
import LLMFilterBox from "src/sections/projects/LLMTracing/LLMFilterBox";
import DateRangePill, {
  dateFilterForOption,
} from "src/sections/projects/LLMTracing/DateRangePill";
import FilterChips from "src/sections/projects/LLMTracing/FilterChips";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import CallLogsGrid from "src/sections/agents/CallLogs/CallLogsGrid";
import SelectAllBanner from "src/sections/projects/LLMTracing/SelectAllBanner";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { PROJECT_SOURCE } from "src/utils/constants";
import {
  apiFilterHasValue,
  apiOpToPanel,
  isNumberFilterOp,
  isRangeFilterOp,
  normalizeApiFilterOp,
  panelOperatorAndValueToApi,
} from "src/sections/annotations/queues/utils/filter-operators";

// ---------------------------------------------------------------------------
// TraceFilterPanel ↔ API filter converters (mirror ObserveToolbar's inline
// logic). Moved here so the dialog's Trace and Span selectors can mount the
// same popover the main tracing page uses.
// ---------------------------------------------------------------------------
const PANEL_TYPE_TO_API = {
  string: "text",
  number: "number",
  boolean: "boolean",
  categorical: "categorical",
  text: "text",
};
const PANEL_CAT_TO_COL_TYPE = {
  attribute: "SPAN_ATTRIBUTE",
  system: "SYSTEM_METRIC",
  eval: "EVAL_METRIC",
  annotation: "ANNOTATION",
};
const COL_TYPE_TO_PANEL_CAT = {
  SPAN_ATTRIBUTE: "attribute",
  SYSTEM_METRIC: "system",
  EVAL_METRIC: "eval",
  ANNOTATION: "annotation",
};

function panelFilterToApi(panel) {
  const { filterOp, filterValue } = panelOperatorAndValueToApi(
    panel.operator,
    panel.value,
  );
  const filterType = PANEL_TYPE_TO_API[panel.fieldType] || "text";
  const colType = PANEL_CAT_TO_COL_TYPE[panel.fieldCategory];
  return {
    columnId: panel.field,
    ...(panel.fieldName && { displayName: panel.fieldName }),
    filterConfig: {
      filterType,
      filterOp,
      filterValue,
      // `col_type` (snake_case) matches the Zod schema in
      // ComplexFilter/common.js — a `colType` key would be stripped by
      // safeParse, which is how `ended_reason` ended up falling through
      // the SYSTEM_METRIC → VOICE_SYSTEM_METRIC_STR_MAP path and
      // generating an "Unknown identifier" ClickHouse error.
      ...(colType && { col_type: colType }),
    },
    _meta: { parentProperty: "" },
  };
}

function apiFilterToPanel(api) {
  const rawOp = api?.filterConfig?.filterOp || "equals";
  const canonicalOp = normalizeApiFilterOp(rawOp);
  const isNumberOp = isNumberFilterOp(canonicalOp);
  const isRange = isRangeFilterOp(canonicalOp);
  const rawVal = api?.filterConfig?.filterValue;
  let value;
  if (isRange && rawVal) {
    value = Array.isArray(rawVal)
      ? rawVal.map((v) => String(v))
      : String(rawVal)
          .split(",")
          .map((v) => v.trim());
  } else if (isNumberOp) {
    value = rawVal != null ? String(rawVal) : "";
  } else if (Array.isArray(rawVal)) {
    value = rawVal.map((v) => String(v));
  } else {
    value = rawVal
      ? String(rawVal)
          .split(",")
          .map((v) => v.trim())
      : [];
  }
  const rawColType =
    api?.filterConfig?.col_type ||
    api?.filterConfig?.colType ||
    api?.col_type ||
    api?.colType ||
    "SYSTEM_METRIC";
  const filterType = api?.filterConfig?.filterType;
  const fieldType = isNumberOp
    ? "number"
    : filterType === "number"
      ? "number"
      : filterType === "categorical"
        ? "categorical"
        : filterType === "text" && rawColType === "ANNOTATION"
          ? "text"
          : "string";
  return {
    field: api.columnId,
    fieldName: api.displayName,
    fieldCategory: COL_TYPE_TO_PANEL_CAT[rawColType] || "system",
    fieldType,
    operator: apiOpToPanel(canonicalOp, fieldType),
    value,
  };
}
import {
  getSessionListColumnDef,
  filterDefinition as sessionFilterDefinition,
  defaultFilter as sessionDefaultFilterBase,
} from "src/sections/projects/SessionsView/common";
import "src/styles/clean-data-table.css";
import { fetchRootSpans } from "src/api/project/llm-tracing";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const DATASET_ROWS_LIMIT = 10;
const TRACE_ROWS_LIMIT = 20;
const DEFAULT_MIN_WIDTH = 300;

const DATASET_GRID_THEME_PARAMS = {
  columnBorder: true,
  headerHeight: "39px",
  wrapperBorderRadius: 4,
};
const SELECTOR_GRID_THEME_PARAMS = {
  columnBorder: false,
  headerColumnBorder: { width: 0 },
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 4,
};

const SOURCE_TYPES = [
  {
    value: "dataset_row",
    label: "From Datasets",
    description: "Choose dataset or select specific datapoints to annotate",
    icon: "/assets/icons/navbar/hugeicons.svg",
    enabled: true,
  },
  {
    value: "trace",
    label: "From Traces",
    description: "Select traces that need to be annotated",
    icon: "/assets/icons/navbar/ic_observe.svg",
    enabled: true,
  },
  {
    value: "observation_span",
    label: "From Spans",
    description: "Select individual spans to annotate",
    icon: "/assets/icons/navbar/ic_dash_tasks.svg",
    enabled: true,
  },
  {
    value: "trace_session",
    label: "From Sessions",
    description: "Select sessions that need to be annotated",
    icon: "/assets/icons/ic_chat_single.svg",

    enabled: true,
  },
  {
    value: "call_execution",
    label: "From Simulation",
    description: "Select simulated voice or chat recordings to annotate",
    icon: "/assets/icons/navbar/ic_optimize.svg",
    enabled: true,
  },
];

// ---------------------------------------------------------------------------
// Fetch all row IDs from a dataset (paginating through all pages)
// ---------------------------------------------------------------------------
const MAX_PAGINATION_PAGES = 100;

async function fetchAllDatasetRowIds(
  queryClient,
  datasetId,
  excludedIds,
  filters,
  search,
) {
  const validFilters = (filters || [])
    .filter(validateFilter)
    .map(transformFilter);
  const allIds = [];
  let page = 0;
  let hasMore = true;

  while (hasMore && page < MAX_PAGINATION_PAGES) {
    const queryOptions = getDatasetQueryOptions(
      datasetId,
      page,
      validFilters,
      [],
      search || "",
      { enabled: true, staleTime: 30000, pageSize: DATASET_ROWS_LIMIT },
    );
    const data = await queryClient.fetchQuery(queryOptions);
    const rows = data?.data?.result?.table ?? [];
    const totalRows = data?.data?.result?.metadata?.total_rows ?? 0;

    rows.forEach((row) => {
      if (row.row_id && !excludedIds.has(row.row_id)) {
        allIds.push(row.row_id);
      }
    });

    page += 1;
    hasMore = page * DATASET_ROWS_LIMIT < totalRows;
  }

  return allIds;
}

const SPAN_ROWS_LIMIT = 20;

// ---------------------------------------------------------------------------
// Fetch all trace IDs / span IDs matching the current filters, paginating
// through the list endpoints. Mirrors fetchAllDatasetRowIds — used by the
// selectAll enumeration path when the backend filter-mode resolver isn't
// available for a source type.
// ---------------------------------------------------------------------------
async function fetchAllTraceIds(
  projectId,
  excludedIds,
  filters,
  projectVersionId,
) {
  const serializedFilters = JSON.stringify(objectCamelToSnake(filters || []));
  const allIds = [];
  const excluded = excludedIds || new Set();
  let page = 0;
  let hasMore = true;

  while (hasMore && page < MAX_PAGINATION_PAGES) {
    const resp = await axios.get(endpoints.project.getTraceList(), {
      params: {
        project: projectId,
        project_version_id: projectVersionId,
        page_number: page,
        page_size: TRACE_ROWS_LIMIT,
        filters: serializedFilters,
      },
    });
    const res = resp?.data?.result;
    const rows = res?.table ?? [];
    const totalRows = res?.metadata?.totalRows ?? 0;

    rows.forEach((row) => {
      const id = row.rowId || row.trace_id || row.id;
      if (id && !excluded.has(id)) allIds.push(id);
    });

    page += 1;
    hasMore = page * TRACE_ROWS_LIMIT < totalRows;
  }

  return allIds;
}

async function fetchAllSpanIds(
  projectId,
  excludedIds,
  filters,
  projectVersionId,
) {
  const serializedFilters = JSON.stringify(objectCamelToSnake(filters || []));
  const allIds = [];
  const excluded = excludedIds || new Set();
  let page = 0;
  let hasMore = true;

  while (hasMore && page < MAX_PAGINATION_PAGES) {
    const resp = await axios.get(endpoints.project.getSpanList(), {
      params: {
        project: projectId,
        project_version_id: projectVersionId,
        page_number: page,
        page_size: SPAN_ROWS_LIMIT,
        filters: serializedFilters,
      },
    });
    const res = resp?.data?.result;
    const rows = res?.table ?? [];
    const totalRows = res?.metadata?.totalRows ?? 0;

    rows.forEach((row) => {
      const id = row.rowId || row.span_id || row.id;
      if (id && !excluded.has(id)) allIds.push(id);
    });

    page += 1;
    hasMore = page * SPAN_ROWS_LIMIT < totalRows;
  }

  return allIds;
}

// ---------------------------------------------------------------------------
// Main component – Drawer-based
// ---------------------------------------------------------------------------
export default function AddItemsDialog({ open, onClose, queueId, queue }) {
  const [sourceType, setSourceType] = useState(null);
  // Selection can be in two modes:
  // 'manual' – individual IDs tracked in selectedIds
  // 'selectAll' – all rows selected, minus excludedIds tracked in selectAllInfo
  const [selectionMode, setSelectionMode] = useState("manual");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [selectAllInfo, setSelectAllInfo] = useState(null);
  // Voice/simulator projects route trace selections through CallLogsGrid;
  // those selections must keep `source_type: "trace"` (matching the voice
  // obs page's "Add to queue") instead of being converted to root spans.
  const [isVoiceTraceSelection, setIsVoiceTraceSelection] = useState(false);
  const [isResolving, setIsResolving] = useState(false);
  const { mutate: addItems, isPending } = useAddQueueItems();
  const queryClient = useQueryClient();
  const isDefaultQueue = !!queue?.is_default;

  const selectionCount =
    selectionMode === "selectAll" && selectAllInfo
      ? selectAllInfo.totalCount - selectAllInfo.excludedIds.size
      : selectedIds.size;

  const handleSetSelection = useCallback((ids) => {
    setSelectionMode("manual");
    setSelectedIds(new Set(ids));
  }, []);

  const handleSelectAll = useCallback((info) => {
    setSelectionMode("selectAll");
    setSelectAllInfo(info);
  }, []);

  const resetSelection = useCallback(() => {
    setSelectionMode("manual");
    setSelectedIds(new Set());
    setSelectAllInfo(null);
    setIsVoiceTraceSelection(false);
  }, []);

  const handleSubmit = async () => {
    try {
      // Filter-mode selectAll for trace/span/session/call_execution:
      // let the backend resolve the full match set server-side (one POST,
      // no client-side pagination, no 500-item batching). Dataset rows
      // are not covered by the Phase 1-9 resolvers, so they keep the
      // enumerated path.
      const isBackendFilterMode =
        selectionMode === "selectAll" &&
        selectAllInfo &&
        (sourceType === "trace" ||
          sourceType === "observation_span" ||
          sourceType === "trace_session" ||
          sourceType === "call_execution");

      if (isBackendFilterMode) {
        const totalCount =
          selectAllInfo.totalCount - selectAllInfo.excludedIds.size;
        addItems(
          {
            queueId,
            selection: {
              mode: "filter",
              source_type: sourceType,
              project_id: selectAllInfo.projectId,
              filter: selectAllInfo.filters || [],
              exclude_ids: Array.from(selectAllInfo.excludedIds || []),
              ...(sourceType === "trace" && isVoiceTraceSelection
                ? { is_voice_call: true }
                : {}),
            },
          },
          {
            onSuccess: () => {
              enqueueSnackbar(
                `${totalCount} item${totalCount !== 1 ? "s" : ""} added to queue`,
                { variant: "success" },
              );
              resetSelection();
              setSourceType(null);
              onClose();
            },
          },
        );
        return;
      }

      let itemsToAdd;
      if (selectionMode === "selectAll" && selectAllInfo) {
        // Dataset-row selectAll still enumerates client-side — no backend
        // filter-mode resolver for datasets yet.
        setIsResolving(true);
        try {
          let allIds;
          if (sourceType === "dataset_row") {
            allIds = await fetchAllDatasetRowIds(
              queryClient,
              selectAllInfo.datasetId,
              selectAllInfo.excludedIds,
              selectAllInfo.filters,
              selectAllInfo.search,
            );
            itemsToAdd = allIds.map((id) => ({
              source_type: "dataset_row",
              source_id: id,
            }));
          } else if (sourceType === "observation_span") {
            allIds = await fetchAllSpanIds(
              selectAllInfo.projectId,
              selectAllInfo.excludedIds,
              selectAllInfo.filters,
              selectAllInfo.projectVersionId,
            );
            itemsToAdd = allIds.map((id) => ({
              source_type: "observation_span",
              source_id: id,
            }));
          } else {
            // sourceType === "trace": fetch trace IDs then convert to root spans
            const traceIds = await fetchAllTraceIds(
              selectAllInfo.projectId,
              selectAllInfo.excludedIds,
              selectAllInfo.filters,
              selectAllInfo.projectVersionId,
            );
            const rootSpanMap = await fetchRootSpans(traceIds);
            const mappedIds = traceIds
              .map((traceId) => rootSpanMap[traceId])
              .filter(Boolean);
            const droppedCount = traceIds.length - mappedIds.length;
            if (droppedCount > 0) {
              enqueueSnackbar(
                `${droppedCount} trace${droppedCount !== 1 ? "s" : ""} skipped — no root span found yet`,
                { variant: "warning" },
              );
            }
            itemsToAdd = mappedIds.map((id) => ({
              source_type: "observation_span",
              source_id: id,
            }));
          }
        } finally {
          setIsResolving(false);
        }
      } else {
        let ids = Array.from(selectedIds);
        let effectiveSourceType = sourceType;

        // Voice/simulator projects keep `source_type: "trace"` — matches
        // the "Add to queue" flow on the voice observability page so the
        // queue badge says "Trace" (not "Span") and the annotator drawer
        // resolves the call via the trace FK. For regular tracing
        // projects, remap trace -> root span so the queue items match the
        // annotator workspace's span-oriented UI (consistent with the
        // ``mappedIds`` branch above at lines 540-548).
        if (sourceType === "trace" && !isVoiceTraceSelection) {
          const rootSpanMap = await fetchRootSpans(ids);
          const originalCount = ids.length;
          ids = ids.map((traceId) => rootSpanMap[traceId]).filter(Boolean);
          effectiveSourceType = "observation_span";
          const droppedCount = originalCount - ids.length;
          if (droppedCount > 0) {
            enqueueSnackbar(
              `${droppedCount} trace${droppedCount !== 1 ? "s" : ""} skipped — no root span found yet`,
              { variant: "warning" },
            );
          }
        }

        itemsToAdd = ids.map((id) => ({
          source_type: effectiveSourceType,
          source_id: id,
        }));
      }

      // Batch enumerated payloads into chunks of 500
      const BATCH_SIZE = 500;
      const totalCount = itemsToAdd.length;
      if (totalCount > BATCH_SIZE) {
        for (let i = 0; i < totalCount; i += BATCH_SIZE) {
          const batch = itemsToAdd.slice(i, i + BATCH_SIZE);
          await new Promise((resolve, reject) => {
            addItems(
              { queueId, items: batch },
              { onSuccess: resolve, onError: reject },
            );
          });
        }
        enqueueSnackbar(`${totalCount} items added to queue`, {
          variant: "success",
        });
        resetSelection();
        setSourceType(null);
        onClose();
      } else {
        addItems(
          { queueId, items: itemsToAdd },
          {
            onSuccess: () => {
              enqueueSnackbar(
                `${totalCount} item${totalCount !== 1 ? "s" : ""} added to queue`,
                { variant: "success" },
              );
              resetSelection();
              setSourceType(null);
              onClose();
            },
          },
        );
      }
    } catch (err) {
      enqueueSnackbar(
        err?.message || "Failed to add items. Please try again.",
        { variant: "error" },
      );
    }
  };

  const handleBack = () => {
    setSourceType(null);
    resetSelection();
  };

  const handleClose = () => {
    setSourceType(null);
    resetSelection();
    onClose();
  };

  const sourceLabel =
    {
      dataset_row: "Choose from dataset",
      trace: "Choose from traces",
      observation_span: "Choose from spans",
      trace_session: "Choose from sessions",
      call_execution: "Choose from simulation",
    }[sourceType] || "Choose items";
  const sourceSubtitle =
    {
      dataset_row: "Choose a dataset to add datapoints from",
      trace: "Choose a project to add traces from",
      observation_span: "Choose a project to add spans from",
      trace_session: "Choose a project to add sessions from",
      call_execution: "Choose a test and execution run to add calls from",
    }[sourceType] || "";

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          width: { xs: "100%", md: "calc(100% - 178px)" },
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          borderRadius: "0 !important",
        },
      }}
    >
      {/* Source type selection (step 1) */}
      {!sourceType && (
        <SourceTypeSelection
          onClose={handleClose}
          onSelect={setSourceType}
          isDefaultQueue={isDefaultQueue}
        />
      )}

      {/* Dataset / Trace selection (step 2) */}
      {sourceType && (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Header */}
          <Box
            sx={{
              px: 3,
              py: 2,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1.5,
              flexWrap: "wrap",
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                minWidth: 0,
                flex: "1 1 280px",
              }}
            >
              <IconButton size="small" onClick={handleBack}>
                <Iconify icon="eva:arrow-ios-back-fill" width={20} />
              </IconButton>
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="h6" noWrap sx={{ minWidth: 0 }}>
                  {sourceLabel}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {sourceSubtitle}
                </Typography>
              </Box>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <IconButton onClick={handleClose}>
                <Iconify icon="mingcute:close-line" width={20} />
              </IconButton>
            </Box>
          </Box>

          {/* Content */}
          <Box
            sx={{
              flex: 1,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              px: 3,
            }}
          >
            {isDefaultQueue && (
              <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
                This is a default queue. Direct annotations still land here
                automatically, and you can add items from any selected source.
              </Alert>
            )}
            {sourceType === "dataset_row" && (
              <DatasetRowSelector
                onSetSelection={handleSetSelection}
                onSelectAll={handleSelectAll}
              />
            )}
            {sourceType === "trace" && (
              <TraceSelector
                onSetSelection={handleSetSelection}
                onSelectAll={handleSelectAll}
                onVoiceProjectChange={setIsVoiceTraceSelection}
              />
            )}
            {sourceType === "observation_span" && (
              <SpanSelector
                onSetSelection={handleSetSelection}
                onSelectAll={handleSelectAll}
              />
            )}
            {sourceType === "trace_session" && (
              <SessionSelector onSetSelection={handleSetSelection} />
            )}
            {sourceType === "call_execution" && (
              <SimulationSelector onSetSelection={handleSetSelection} />
            )}
          </Box>

          {/* Footer with actions */}
          <Box
            sx={{
              px: 3,
              py: 1.5,
              borderTop: "1px solid",
              borderColor: "divider",
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              flexWrap: "wrap",
              flexShrink: 0,
            }}
          >
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ flex: "1 1 220px", minWidth: 0 }}
            >
              {selectionCount === 0
                ? "Select rows with the checkbox column to add them."
                : `${selectionCount} selected`}
            </Typography>
            <Button
              variant="outlined"
              color="primary"
              onClick={handleClose}
              disabled={isPending || isResolving}
              sx={{ minWidth: 140, flexShrink: 0 }}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              color="primary"
              onClick={handleSubmit}
              disabled={selectionCount === 0 || isPending || isResolving}
              startIcon={
                isPending || isResolving ? (
                  <CircularProgress size={16} />
                ) : undefined
              }
              sx={{ minWidth: 140, flexShrink: 0 }}
            >
              {selectionCount > 0
                ? `(${selectionCount}) Add to queue`
                : "Add to queue"}
            </Button>
          </Box>
        </Box>
      )}
    </Drawer>
  );
}

AddItemsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  queueId: PropTypes.string.isRequired,
  queue: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Source Type Selection (Step 1)
// ---------------------------------------------------------------------------
function SourceTypeSelection({ onSelect, onClose, isDefaultQueue }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", flex: 1 }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          p: 2,
          flexShrink: 0,
        }}
      >
        <IconButton
          onClick={onClose}
          sx={{
            color: "text.primary",
          }}
          size="small"
        >
          <SvgColor
            sx={{
              height: "24px",
              width: "24px",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </Box>
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          p: 4,
        }}
      >
        <Typography variant="h5" sx={{ mb: 1 }}>
          Add your first annotation project
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mb: 4, textAlign: "center" }}
        >
          Add datasets, traces or spans to this queue. These items will be
          queued for human annotation.{" "}
          <Typography
            component="a"
            variant="body2"
            href="#"
            sx={{ color: "primary.main" }}
          >
            Check docs
          </Typography>
        </Typography>
        {isDefaultQueue && (
          <Alert
            severity="info"
            variant="outlined"
            sx={{ width: "100%", maxWidth: 560, mb: 2 }}
          >
            This default queue auto-receives direct annotations for its default
            source, but you can add items from any source here.
          </Alert>
        )}

        <Box
          sx={{
            width: "100%",
            maxWidth: 560,
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
          }}
        >
          {SOURCE_TYPES.map((src) => (
            <Box
              key={src.value}
              onClick={src.enabled ? () => onSelect(src.value) : undefined}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,
                px: 2.5,
                py: 2,
                display: "flex",
                alignItems: "center",
                gap: 2,
                cursor: src.enabled ? "pointer" : "not-allowed",
                opacity: src.enabled ? 1 : 0.9,
                transition: "all 0.15s",
                "&:hover": src.enabled
                  ? { borderColor: "primary.main", bgcolor: "action.hover" }
                  : {},
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 40,
                  height: 40,
                  borderRadius: 0.5,
                  bgcolor: "action.hover",
                  color: "primary.main",
                }}
              >
                {src.icon.startsWith("/") ? (
                  <SvgColor
                    src={src.icon}
                    sx={{
                      width: 24,
                      height: 24,
                      color: "primary.main",
                    }}
                  />
                ) : (
                  <Iconify icon={src.icon} width={24} color="primary.main" />
                )}
              </Box>
              <Box sx={{ flex: 1 }}>
                <Typography variant="subtitle2">{src.label}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {src.description}
                </Typography>
                {!src.enabled && (
                  <Chip
                    label="Coming soon"
                    size="small"
                    sx={{ ml: 1, height: 20, fontSize: 10 }}
                  />
                )}
              </Box>
              {src.enabled && (
                <Iconify
                  icon="eva:arrow-ios-forward-fill"
                  width={20}
                  sx={{ color: "text.secondary" }}
                />
              )}
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
}

SourceTypeSelection.propTypes = {
  onSelect: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  isDefaultQueue: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Build read-only column defs that match the dataset view exactly
// ---------------------------------------------------------------------------
function buildReadOnlyColumnDefs(columnConfig) {
  return columnConfig
    .filter((col) => col.isVisible !== false)
    .map((col) => ({
      field: col.id,
      headerName: col.name,
      minWidth: DEFAULT_MIN_WIDTH,
      resizable: true,
      sortable: true,
      editable: false,
      cellDataType: AGGridCellDataType[col.dataType],
      dataType: col.dataType,
      pinned: col.isFrozen,
      hide: !col.isVisible,
      headerComponent: CustomDevelopDetailColumn,
      headerComponentParams: { col, readOnly: true },
      cellRenderer: CustomCellRender,
      cellRendererParams: { editable: false },
      cellStyle: {
        padding: 0,
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
      col: { ...col, isHoverButtonVisible: false },
      valueGetter: (params) => {
        const cellValue = params.data?.[col.id]?.cellValue;
        return parseCellValue(cellValue, AGGridCellDataType[col.dataType]);
      },
    }));
}

// ---------------------------------------------------------------------------
// Server-side datasource for dataset grid (read-only, with filters)
// ---------------------------------------------------------------------------
function createDataSource(
  queryClient,
  datasetId,
  filtersRef,
  searchRef,
  setGridLoading,
) {
  return {
    getRows: async (params) => {
      const { request } = params;
      const pageNumber = Math.floor(request.startRow / DATASET_ROWS_LIMIT);
      const sort = request?.sortModel?.map(({ colId, sort: dir }) => ({
        columnId: colId,
        type: dir === "asc" ? "ascending" : "descending",
      }));

      // Use filters from ref (set by DevelopFilterBox or our own filter state)
      const filters = filtersRef.current || [];
      const validFilters = filters.filter(validateFilter).map(transformFilter);
      const search = searchRef.current || "";

      try {
        setGridLoading?.(true);
        const queryOptions = getDatasetQueryOptions(
          datasetId,
          pageNumber,
          validFilters,
          sort,
          search,
          { enabled: true, staleTime: 0, pageSize: DATASET_ROWS_LIMIT },
        );
        const data = await queryClient.fetchQuery({ ...queryOptions });
        const rows = data?.data?.result?.table ?? [];
        const totalRows = data?.data?.result?.metadata?.total_rows ?? 0;

        params.api.setGridOption("context", {
          totalRowCount: totalRows,
        });

        params.success({
          rowData: rows,
          rowCount: totalRows,
        });
      } catch {
        params.fail();
      } finally {
        setGridLoading?.(false);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Status bar component
// ---------------------------------------------------------------------------
function StatusBar({ api }) {
  const [loadedRows, setLoadedRows] = useState(0);
  const [totalRows, setTotalRows] = useState(0);

  useEffect(() => {
    if (!api) return;
    const updateCounts = () => {
      const context = api.getGridOption?.("context");
      const total = context?.totalRowCount ?? api.getDisplayedRowCount();
      setTotalRows(total);
      setLoadedRows(api.getLastDisplayedRowIndex() + 1);
    };
    updateCounts();
    const events = ["modelUpdated", "viewportChanged", "firstDataRendered"];
    events.forEach((e) => api.addEventListener(e, updateCounts));
    return () => {
      if (!api.isDestroyed()) {
        events.forEach((e) => api.removeEventListener(e, updateCounts));
      }
    };
  }, [api]);

  return (
    <Box sx={{ px: 2, py: 1, fontSize: 13, color: "text.secondary" }}>
      Showing Rows: {loadedRows} / Total Rows: {totalRows}
    </Box>
  );
}

StatusBar.propTypes = {
  api: PropTypes.object,
};

function FieldLoadingAdornment({ loading }) {
  if (!loading) return null;
  return (
    <InputAdornment position="end" sx={{ mr: 2 }}>
      <CircularProgress size={16} thickness={4} />
    </InputAdornment>
  );
}

FieldLoadingAdornment.propTypes = {
  loading: PropTypes.bool,
};

function SelectorEmptyState({
  loading,
  title,
  description,
  requiredLabel,
  loadingLabel,
}) {
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        color: "text.secondary",
        px: 2,
      }}
    >
      {loading ? (
        <CircularProgress size={28} sx={{ mb: 2 }} />
      ) : (
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            mb: 1.5,
            color: "text.secondary",
          }}
        >
          <Typography component="span" color="error.main" fontWeight={700}>
            *
          </Typography>
        </Box>
      )}
      <Typography variant="h6" color="text.primary" sx={{ mb: 0.75 }}>
        {loading ? loadingLabel || title : title}
      </Typography>
      <Typography variant="body2" sx={{ maxWidth: 420 }}>
        {description}
      </Typography>
      {!loading && requiredLabel && (
        <Typography variant="caption" sx={{ mt: 1.25 }}>
          Please select{" "}
          <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
            {requiredLabel}
          </Box>{" "}
          to move forward.
        </Typography>
      )}
    </Box>
  );
}

SelectorEmptyState.propTypes = {
  loading: PropTypes.bool,
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  requiredLabel: PropTypes.string,
  loadingLabel: PropTypes.string,
};

function GridLoadingOverlay({ open, label = "Loading rows..." }) {
  if (!open) return null;
  return (
    <Box
      sx={{
        position: "absolute",
        inset: 0,
        zIndex: 2,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: (theme) => alpha(theme.palette.background.paper, 0.72),
        backdropFilter: "blur(1px)",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <CircularProgress size={18} />
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
      </Box>
    </Box>
  );
}

GridLoadingOverlay.propTypes = {
  open: PropTypes.bool,
  label: PropTypes.string,
};

// ---------------------------------------------------------------------------
// Dataset Row Selector – Same AG Grid as dataset view
// ---------------------------------------------------------------------------
function DatasetRowSelector({ onSetSelection, onSelectAll }) {
  const [datasetId, setDatasetId] = useState("");
  const [search, setSearch] = useState("");
  const [gridApi, setGridApi] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filters, setFiltersState] = useState([
    { ...DefaultFilter, id: getRandomId() },
  ]);
  const [isGridLoading, setIsGridLoading] = useState(false);
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(DATASET_GRID_THEME_PARAMS);
  const queryClient = useQueryClient();
  const filtersRef = useRef([]);
  const searchRef = useRef("");

  const {
    data: datasets,
    isLoading: isDatasetsLoading,
    isFetching: isDatasetsFetching,
  } = useQuery({
    queryKey: ["datasets-list-simple"],
    queryFn: () => axios.get("/model-hub/develops/get-datasets-names/"),
    select: (d) => d.data?.result?.datasets || [],
    staleTime: 1000 * 60 * 5,
  });

  // Get column config from page 0
  const {
    data: tableData,
    isLoading: isTableLoading,
    isFetching: isTableFetching,
  } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], "", {
      enabled: !!datasetId,
      staleTime: Infinity,
    }),
  );

  const columnConfig = useMemo(
    () => tableData?.data?.result?.columnConfig ?? [],
    [tableData],
  );

  const columnDefs = useMemo(
    () => buildReadOnlyColumnDefs(columnConfig),
    [columnConfig],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: true,
      cellStyle: {
        padding: 0,
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
    }),
    [],
  );

  const selectionColumnDef = useMemo(
    () => ({ pinned: true, lockPinned: true }),
    [],
  );

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
      if (datasetId) {
        const ds = createDataSource(
          queryClient,
          datasetId,
          filtersRef,
          searchRef,
          setIsGridLoading,
        );
        params.api.setGridOption("serverSideDatasource", ds);
      }
    },
    [datasetId, queryClient],
  );

  // Refresh datasource when dataset changes
  useEffect(() => {
    if (gridApi && datasetId) {
      const ds = createDataSource(
        queryClient,
        datasetId,
        filtersRef,
        searchRef,
        setIsGridLoading,
      );
      gridApi.setGridOption("serverSideDatasource", ds);
    }
  }, [datasetId, gridApi, queryClient]);

  // Handle search
  const handleSearchKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter" && gridApi) {
        searchRef.current = search;
        const ds = createDataSource(
          queryClient,
          datasetId,
          filtersRef,
          searchRef,
          setIsGridLoading,
        );
        gridApi.setGridOption("serverSideDatasource", ds);
      }
    },
    [gridApi, search, datasetId, queryClient],
  );

  // Handle row selection — detect select-all via getServerSideSelectionState
  const onSelectionChanged = useCallback(
    (event) => {
      const api = event.api;
      const selState = api.getServerSideSelectionState();

      if (selState.selectAll) {
        // All rows selected (minus toggled-off nodes)
        const context = api.getGridOption("context");
        const totalCount = context?.totalRowCount ?? 0;
        const excludedIds = new Set(selState.toggledNodes || []);
        onSelectAll({
          datasetId,
          totalCount,
          excludedIds,
          filters: filtersRef.current,
          search: searchRef.current,
        });
      } else {
        // Individual selection — collect from loaded nodes
        const ids = [];
        api.forEachNode((node) => {
          if (node.isSelected() && node.data?.rowId) {
            ids.push(node.data.row_id);
          }
        });
        onSetSelection(ids);
      }
    },
    [onSetSelection, onSelectAll, datasetId],
  );

  // Refresh grid when filters change
  const refreshGrid = useCallback(() => {
    if (gridApi && datasetId) {
      const ds = createDataSource(
        queryClient,
        datasetId,
        filtersRef,
        searchRef,
        setIsGridLoading,
      );
      gridApi.setGridOption("serverSideDatasource", ds);
    }
  }, [gridApi, datasetId, queryClient]);

  const setFilters = useCallback(
    (filterFn) => {
      const oldValid = filtersRef.current
        .filter(validateFilter)
        .map(transformFilter);
      const newFilters =
        typeof filterFn === "function" ? filterFn(filters) : filterFn;
      setFiltersState(newFilters);
      filtersRef.current = newFilters;
      const newValid = newFilters.filter(validateFilter).map(transformFilter);
      if (!isEqual(oldValid, newValid)) {
        refreshGrid();
      }
    },
    [filters, refreshGrid],
  );

  // Build allColumns for filter row (same shape DevelopFilterRow expects)
  const allColumns = useMemo(
    () =>
      columnDefs.map((cd) => ({
        field: cd.field,
        headerName: cd.headerName,
        col: columnConfig.find((c) => c.id === cd.field) || {
          dataType: "text",
        },
      })),
    [columnDefs, columnConfig],
  );

  const isFilterApplied = useMemo(
    () =>
      filters.some((f) =>
        f.filterConfig?.filterValue && Array.isArray(f.filterConfig.filterValue)
          ? f.filterConfig.filterValue.length > 0
          : f.filterConfig.filterValue !== "",
      ),
    [filters],
  );

  const handleDatasetChange = (e) => {
    setDatasetId(e.target.value);
    setSearch("");
    searchRef.current = "";
    filtersRef.current = [];
    setFiltersState([{ ...DefaultFilter, id: getRandomId() }]);
    setFilterOpen(false);
  };

  const isDatasetListLoading =
    isDatasetsLoading || (isDatasetsFetching && !datasets);
  const isDatasetSchemaLoading =
    !!datasetId &&
    (isTableLoading || (isTableFetching && columnDefs.length === 0));

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      {/* Dataset picker + search bar */}
      <Box
        sx={{
          py: 2,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
          flexShrink: 0,
        }}
      >
        <TextField
          select
          size="small"
          label="Dataset"
          value={datasetId}
          onChange={handleDatasetChange}
          sx={{ minWidth: 220, flex: "1 1 260px" }}
          required
          SelectProps={{
            MenuProps: {
              PaperProps: {
                style: { maxHeight: 300, overflowY: "auto" },
              },
            },
          }}
          InputProps={{
            endAdornment: (
              <FieldLoadingAdornment loading={isDatasetListLoading} />
            ),
          }}
          helperText={
            !datasetId ? "Required. Select a dataset to continue." : " "
          }
        >
          <MenuItem value="" disabled>
            {isDatasetListLoading ? "Loading datasets..." : "Choose a dataset"}
          </MenuItem>
          {isDatasetListLoading && (
            <MenuItem disabled>
              <CircularProgress size={14} sx={{ mr: 1 }} />
              Loading datasets...
            </MenuItem>
          )}
          {(datasets || []).map((ds) => (
            <MenuItem key={ds.datasetId || ds.id} value={ds.datasetId || ds.id}>
              {ds.name}
            </MenuItem>
          ))}
        </TextField>

        {datasetId && (
          <>
            <Box sx={{ flex: "1 1 auto", minWidth: 0 }} />
            <IconButton
              size="small"
              onClick={() => setFilterOpen((v) => !v)}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: (theme) =>
                  isFilterApplied
                    ? alpha(theme.palette.primary.main, 0.12)
                    : "transparent",
              }}
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_filter.svg"
                sx={{
                  width: 16,
                  height: 16,
                  color: isFilterApplied ? "primary.main" : "text.primary",
                }}
              />
            </IconButton>
            <TextField
              size="small"
              placeholder="Search in dataset"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              sx={{ minWidth: 180, flex: "1 1 220px" }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="eva:search-fill"
                      sx={{ color: "text.disabled" }}
                      width={18}
                    />
                  </InputAdornment>
                ),
              }}
            />
          </>
        )}
      </Box>

      {/* Filter box */}
      {datasetId && filterOpen && (
        <Box sx={{ px: 1.5, pb: 1, flexShrink: 0 }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            {filters.map((filter, index) => (
              <DevelopFilterRow
                key={filter.id}
                index={index}
                filter={filter}
                allColumns={allColumns}
                removeFilter={(id) => {
                  if (filters.length === 1) {
                    setFilterOpen(false);
                    setFilters([{ ...DefaultFilter, id: getRandomId() }]);
                  } else {
                    setFilters((prev) => prev.filter((f) => f.id !== id));
                  }
                }}
                addFilter={() => {
                  setFilters((prev) => [
                    ...prev,
                    { ...DefaultFilter, id: getRandomId() },
                  ]);
                }}
                updateFilter={(id, newFilter) => {
                  setFilters((prev) =>
                    prev.map((f) =>
                      f.id === id
                        ? typeof newFilter === "function"
                          ? newFilter(f)
                          : newFilter
                        : f,
                    ),
                  );
                }}
              />
            ))}
          </Box>
        </Box>
      )}

      {/* Empty state */}
      {!datasetId && (
        <SelectorEmptyState
          loading={isDatasetListLoading}
          loadingLabel="Loading datasets..."
          title="Select a dataset"
          description="Choose a dataset from the dropdown above to load its datapoints."
          requiredLabel="Dataset"
        />
      )}

      {datasetId && isDatasetSchemaLoading && (
        <SelectorEmptyState
          loading
          title="Loading dataset"
          description="Loading dataset columns and datapoints."
          loadingLabel="Loading dataset..."
        />
      )}

      {/* AG Grid – same as dataset view */}
      {datasetId && columnDefs.length > 0 && (
        <SingleImageViewerProvider>
          <Box
            sx={{
              flex: 1,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Box sx={{ flex: 1, position: "relative" }}>
              <GridLoadingOverlay open={isGridLoading} />
              <AgGridReact
                ref={gridRef}
                rowHeight={100}
                rowSelection={{ mode: "multiRow" }}
                selectionColumnDef={selectionColumnDef}
                theme={agTheme}
                columnDefs={columnDefs}
                defaultColDef={defaultColDef}
                pagination={false}
                cacheBlockSize={DATASET_ROWS_LIMIT}
                rowBuffer={0}
                maxBlocksInCache={5}
                suppressServerSideFullWidthLoadingRow
                serverSideInitialRowCount={10}
                rowModelType="serverSide"
                onGridReady={onGridReady}
                onSelectionChanged={onSelectionChanged}
                getRowId={({ data }) => data.row_id}
                className="develop-data-grid"
                suppressColumnMoveAnimation
                suppressAnimationFrame
                animateRows={false}
              />
            </Box>
            <StatusBar api={gridApi} />
          </Box>
        </SingleImageViewerProvider>
      )}
    </Box>
  );
}

DatasetRowSelector.propTypes = {
  onSetSelection: PropTypes.func.isRequired,
  onSelectAll: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Trace Selector – Same AG Grid as tracer view with server-side row model
// ---------------------------------------------------------------------------

const traceDefaultFilterBase = {
  columnId: "",
  filterConfig: {
    filterType: "",
    filterOp: "",
    filterValue: "",
  },
};

function TraceSelector({ onSetSelection, onSelectAll, onVoiceProjectChange }) {
  const [projectId, setProjectId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [columns, setColumns] = useState([]);
  const [filters, setFilters] = useState([
    { ...traceDefaultFilterBase, id: getRandomId() },
  ]);
  const [dateFilter, setDateFilter] = useState(() => ({
    dateFilter: dateFilterForOption("7D"),
    dateOption: "7D",
  }));
  const [, setFilterDefinition] = useState([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [gridApi, setGridApi] = useState(null);
  const [isGridLoading, setIsGridLoading] = useState(false);
  const gridRef = useRef(null);
  const filterButtonRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const filtersRef = useRef([]);
  // CallLogsGrid client-side paginated selection meta (for the voice
  // branch below). Drives the SelectAllBanner's visibility + count.
  const [simCallMeta, setSimCallMeta] = useState({
    isAllOnPageSelected: false,
    currentPageSize: 0,
    totalPages: 1,
    pageLimit: 25,
  });

  const {
    data: projects,
    isLoading: isProjectsLoading,
    isFetching: isProjectsFetching,
  } = useQuery({
    queryKey: ["projects-list-all-for-traces"],
    queryFn: () => axios.get(endpoints.project.listProjects()),
    select: (d) => d.data?.result?.projects || [],
    staleTime: 1000 * 60 * 5,
  });

  const selectedProject = useMemo(
    () => (projects || []).find((p) => p.id === projectId),
    [projects, projectId],
  );
  const isPrototype = selectedProject?.trace_type === "experiment";

  // Simulator / voice projects render CallLogsGrid (voice-specific
  // columns: Duration / Avg Latency / Turn Count / Talk Ratio / Cost).
  // Matches the main LLM Tracing page for simulator projects.
  const {
    data: projectDetails,
    isLoading: isProjectDetailsLoading,
    isFetching: isProjectDetailsFetching,
  } = useGetProjectDetails(projectId, !!projectId);
  const isVoiceProject = projectDetails?.source === PROJECT_SOURCE.SIMULATOR;

  // Surface voice/simulator state to the parent so handleSubmit knows to
  // keep `source_type: "trace"` instead of resolving to root spans.
  useEffect(() => {
    onVoiceProjectChange?.(isVoiceProject);
  }, [isVoiceProject, onVoiceProjectChange]);

  // Fetch versions for prototype projects
  const {
    data: versions,
    isLoading: isVersionsLoading,
    isFetching: isVersionsFetching,
  } = useQuery({
    queryKey: ["project-versions-dropdown-traces", projectId],
    queryFn: () =>
      axios.get(endpoints.project.runListSearch(), {
        params: { project_id: projectId, page_number: 0, page_size: 200 },
      }),
    select: (d) => d.data?.result?.project_version_ids || [],
    enabled: !!projectId && isPrototype,
    staleTime: 1000 * 60 * 2,
  });

  // Validate & transform filters using the same Zod pipeline as the tracer view.
  // This converts columnId to snake_case, validates filterType/filterOp, and strips invalid filters.
  const validatedMainFilters = useMemo(() => {
    // TraceFilterPanel's output (via panelFilterToApi) is already correct
    // shape — columnId + filterConfig with col_type preserved. Don't run
    // it through the legacy Zod validator in ComplexFilter/common.js:
    // its AllowedOperators enum omits `in` / `not_in` (which we promote
    // to for multi-value equals) so the whole filter gets dropped on
    // second apply. We only need to drop the empty-default row.
    return filters.filter((f) => f?.columnId);
  }, [filters]);

  // Append the date range as a created_at between filter — mirrors
  // `useLLMTracingFilters`. The backend list_traces endpoint + bulk-select
  // resolver both parse it as a standard filter entry.
  const validatedFilters = useMemo(() => {
    const range = dateFilter?.dateFilter;
    if (!range || !range[0] || !range[1]) return validatedMainFilters;
    return [
      ...validatedMainFilters,
      {
        columnId: "created_at",
        filterConfig: {
          filterType: "datetime",
          filterOp: "between",
          filterValue: [
            new Date(range[0]).toISOString(),
            new Date(range[1]).toISOString(),
          ],
        },
      },
    ];
  }, [validatedMainFilters, dateFilter]);

  // Keep filtersRef in sync
  useEffect(() => {
    filtersRef.current = validatedFilters;
  }, [validatedFilters]);

  // Server-side datasource (same pattern as TraceGrid)
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        try {
          const { request } = params;
          const pageSize = request.endRow - request.startRow;
          const pageNumber = Math.floor(request.startRow / pageSize);

          const apiParams = {
            project_id: projectId,
            page_number: pageNumber,
            page_size: TRACE_ROWS_LIMIT,
            filters: JSON.stringify(objectCamelToSnake(filtersRef.current)),
          };
          if (versionId) {
            apiParams.project_version_id = versionId;
          }
          setIsGridLoading(true);
          const results = await axios.get(
            endpoints.project.getTracesForObserveProject(),
            { params: apiParams },
          );
          const res = results?.data?.result;

          // Update columns from response config (same as TraceGrid)
          const newCols = res?.config?.map((o) => ({
            ...o,
            id: o.id,
          }));
          if (newCols) {
            setColumns((prev) => (isEqual(prev, newCols) ? prev : newCols));
          }

          const totalRows = res?.metadata?.total_rows;
          const ctx = params.api.getGridOption("context") || {};
          params.api.setGridOption("context", {
            ...ctx,
            totalRowCount: totalRows,
          });
          params.success({
            rowData: res?.table,
            rowCount: totalRows,
          });
        } catch {
          params.fail();
        } finally {
          setIsGridLoading(false);
        }
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId, versionId, validatedFilters],
  );

  // Build column defs from server config (same as TraceGrid)
  const columnDefs = useMemo(() => {
    if (!columns || columns.length === 0) {
      return TRACE_DEFAULT_COLUMNS;
    }
    return columns
      .filter((col) => !col.groupBy || col.groupBy === "")
      .map((col) => getTraceListColumnDefs(col));
  }, [columns]);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      flex: 1,
      minWidth: 200,
      cellStyle: {
        padding: 0,
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
      suppressSizeToFit: false,
      sortable: false,
    }),
    [],
  );

  // Update filter definition when columns change
  useEffect(() => {
    if (columns.length > 0) {
      const def = generateObserveTraceFilterDefinition(columns, [], null);
      setFilterDefinition(def);
    }
  }, [columns]);

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
      if (projectId) {
        params.api.setGridOption("serverSideDatasource", dataSource);
      }
    },
    [projectId, dataSource],
  );

  // Refresh datasource when project or filters change
  useEffect(() => {
    if (gridApi && projectId) {
      gridApi.setGridOption("serverSideDatasource", dataSource);
    }
  }, [dataSource, gridApi, projectId]);

  // Opt-in for cross-page select-all (mirrors the trace /
  // sessions tab in LLMTracingView — Phase 3 + 7 of the bulk-select
  // revamp). When ag-grid flips into inverted-selection mode we keep
  // the parent in *manual* selection for just the visible page; the
  // SelectAllBanner then offers the user an explicit "Select all N
  // matching your filter" opt-in before we flip to filter-mode.
  const [pageSelectAllMeta, setPageSelectAllMeta] = useState(null);
  const onSelectionChanged = useCallback(
    (event) => {
      const selectionState = event.api.getServerSideSelectionState();

      if (selectionState.selectAll) {
        // ag-grid inverted selection — all rows considered selected,
        // toggledNodes are the user's exclusions. In the server-side
        // row model only loaded blocks have `.data`; `getRenderedNodes`
        // returns exactly the page currently on screen (matches what
        // `forEachNodeAfterFilterAndSort` would yield for a client-
        // side grid).
        const excludedIds = new Set(selectionState.toggledNodes || []);
        const totalCount =
          (event.api.getGridOption("context") || {}).totalRowCount ?? 0;
        const visibleRowIds = [];
        const rendered = event.api.getRenderedNodes?.() || [];
        rendered.forEach((node) => {
          const rowId = node?.data?.trace_id ?? node?.data?.traceId ?? node?.id;
          if (rowId && !excludedIds.has(rowId)) visibleRowIds.push(rowId);
        });
        onSetSelection(visibleRowIds);
        setPageSelectAllMeta({
          totalCount,
          excludedIds,
          visibleCount: visibleRowIds.length,
        });
      } else {
        // Regular manual selection – toggledNodes = selected IDs.
        const ids = selectionState.toggledNodes || [];
        onSetSelection(ids);
        setPageSelectAllMeta(null);
      }
    },
    [onSetSelection],
  );

  const commitFilterModeSelectAll = useCallback(() => {
    if (!pageSelectAllMeta) return;
    onSelectAll({
      totalCount: pageSelectAllMeta.totalCount,
      excludedIds: pageSelectAllMeta.excludedIds,
      projectId,
      projectVersionId: versionId || undefined,
      filters: filtersRef.current,
    });
    setPageSelectAllMeta(null);
  }, [pageSelectAllMeta, onSelectAll, projectId, versionId]);

  const isFilterApplied = useMemo(
    () => filters.some((f) => f.columnId),
    [filters],
  );

  const handleProjectChange = (e) => {
    setProjectId(e.target.value);
    setVersionId("");
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterAnchorEl(null);
    setFilterOpen(false);
    onSetSelection([]);
  };

  const handleVersionChange = (e) => {
    setVersionId(e.target.value);
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterAnchorEl(null);
    setFilterOpen(false);
  };

  // For prototype projects, require a version to be selected before showing grid
  const canShowGrid = projectId && (!isPrototype || versionId);
  const isProjectListLoading =
    isProjectsLoading || (isProjectsFetching && !projects);
  const isVersionListLoading =
    isVersionsLoading || (isVersionsFetching && !versions);
  const isResolvingProjectKind =
    !!projectId &&
    (isProjectDetailsLoading || (isProjectDetailsFetching && !projectDetails));

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      {/* Project picker + version picker + filter button */}
      <Box
        sx={{
          py: 2,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        <Autocomplete
          size="small"
          loading={isProjectListLoading}
          loadingText="Loading projects..."
          options={projects || []}
          getOptionLabel={(p) => p?.name || ""}
          value={(projects || []).find((p) => p.id === projectId) || null}
          onChange={(_, newValue) =>
            handleProjectChange({
              target: { value: newValue?.id || "" },
            })
          }
          isOptionEqualToValue={(opt, val) => opt?.id === val?.id}
          renderOption={(props, option) => (
            <li {...props} key={option.id}>
              {option.name}
              {option.trace_type === "experiment" && (
                <Chip
                  label="Prototype"
                  size="small"
                  sx={{ ml: 1, height: 20, fontSize: 10 }}
                />
              )}
            </li>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Project"
              placeholder="Choose a project"
              required
              helperText={
                !projectId ? "Required. Select a project to continue." : " "
              }
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {isProjectListLoading && (
                      <CircularProgress size={16} sx={{ mr: 1 }} />
                    )}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 220, flex: "1 1 280px" }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 180, flex: "1 1 220px" }}
            required
            InputProps={{
              endAdornment: (
                <FieldLoadingAdornment loading={isVersionListLoading} />
              ),
            }}
            helperText={!versionId ? "Required for prototype projects." : " "}
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              {isVersionListLoading
                ? "Loading versions..."
                : "Choose a version"}
            </MenuItem>
            {isVersionListLoading && (
              <MenuItem disabled>
                <CircularProgress size={14} sx={{ mr: 1 }} />
                Loading versions...
              </MenuItem>
            )}
            {(versions || []).map((v) => (
              <MenuItem key={v.id} value={v.id}>
                {v.name}
              </MenuItem>
            ))}
          </TextField>
        )}

        {canShowGrid && (
          <>
            <Box sx={{ flex: 1 }} />
            <DateRangePill
              dateFilter={dateFilter}
              setDateFilter={setDateFilter}
            />
            <IconButton
              ref={filterButtonRef}
              size="small"
              onClick={() => {
                setFilterAnchorEl(filterButtonRef.current);
                setFilterOpen((v) => !v);
              }}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: (theme) =>
                  isFilterApplied
                    ? alpha(theme.palette.primary.main, 0.12)
                    : "transparent",
              }}
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_filter.svg"
                sx={{
                  width: 16,
                  height: 16,
                  color: isFilterApplied ? "primary.main" : "text.primary",
                }}
              />
            </IconButton>
          </>
        )}
      </Box>

      {/* New trace filter popover — same component as the main LLM Tracing
          page (ObserveToolbar mounts it via `setIsPrimaryFilterOpen`). */}
      {canShowGrid && (
        <TraceFilterPanel
          anchorEl={filterAnchorEl || filterButtonRef.current}
          open={filterOpen}
          onClose={() => setFilterOpen(false)}
          projectId={projectId}
          isSimulator={isVoiceProject}
          currentFilters={validatedMainFilters
            .filter((f) => f?.columnId)
            .map(apiFilterToPanel)}
          onApply={(newPanelFilters) => {
            const apiNext = (newPanelFilters || [])
              .map(panelFilterToApi)
              .filter(apiFilterHasValue);
            setFilters(
              apiNext.length
                ? apiNext.map((f) => ({ ...f, id: getRandomId() }))
                : [{ ...traceDefaultFilterBase, id: getRandomId() }],
            );
          }}
        />
      )}

      {/* Active filter chips (excludes the system-managed created_at entry
          — that's surfaced by the Date pill, not the chip bar) */}
      {canShowGrid && (
        <FilterChips
          extraFilters={(objectCamelToSnake(validatedMainFilters) || []).filter(
            (f) => f?.column_id && f.column_id !== "created_at",
          )}
          onAddFilter={(anchorEl) => {
            setFilterAnchorEl(anchorEl || filterButtonRef.current);
            setFilterOpen(true);
          }}
          onChipClick={(_idx, anchorEl) => {
            setFilterAnchorEl(anchorEl || filterButtonRef.current);
            setFilterOpen(true);
          }}
          onRemoveFilter={(idx) => {
            setFilterAnchorEl(null);
            // FilterChips indexes into the *snake-case validated* list which
            // already stripped empty rows. Map back to the original filters
            // state by matching on columnId + filterConfig.
            const snakeChips = (
              objectCamelToSnake(validatedMainFilters) || []
            ).filter((f) => f?.column_id && f.column_id !== "created_at");
            const target = snakeChips[idx];
            if (!target) return;
            setFilters((prev) =>
              prev.filter((f) => {
                const colMatches = f?.columnId === target.column_id;
                const opMatches =
                  f?.filterConfig?.filterOp ===
                  target?.filter_config?.filter_op;
                return !(colMatches && opMatches);
              }),
            );
          }}
          onClearAll={() => {
            setFilterAnchorEl(null);
            setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
            setFilterOpen(false);
          }}
        />
      )}

      {/* Empty state */}
      {!canShowGrid && (
        <SelectorEmptyState
          loading={!projectId ? isProjectListLoading : isVersionListLoading}
          loadingLabel={
            !projectId ? "Loading projects..." : "Loading versions..."
          }
          title={!projectId ? "Select a project" : "Select a version"}
          description={
            !projectId
              ? "Choose a project from the dropdown above to load its traces."
              : "Choose a version from the dropdown above to load traces."
          }
          requiredLabel={!projectId ? "Project" : "Version"}
        />
      )}

      {canShowGrid && isResolvingProjectKind && (
        <SelectorEmptyState
          loading
          title="Loading project"
          description="Checking project type before loading rows."
          loadingLabel="Loading project..."
        />
      )}

      {/* Voice / simulator projects: use the same CallLogsGrid the main
          LLM Tracing page renders for simulator projects (voice-specific
          columns: Duration, Avg Latency, Turn Count, Talk Ratio, Cost).

          Phase 9 pattern — CallLogsGrid is client-side paginated, so
          clicking the header checkbox only picks the visible page
          (~25 rows). The SelectAllBanner below surfaces the
          cross-page total so the user can opt into filter-mode bulk
          add (same as LLMTracingView's simulator branch). */}
      {canShowGrid && !isResolvingProjectKind && isVoiceProject && (
        <Box
          sx={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <SelectAllBanner
            visible={
              simCallMeta.isAllOnPageSelected && simCallMeta.totalPages > 1
            }
            visibleCount={simCallMeta.currentPageSize}
            totalMatching={simCallMeta.totalPages * simCallMeta.pageLimit}
            noun="call"
            onSelectAll={() => {
              onSelectAll({
                totalCount: simCallMeta.totalPages * simCallMeta.pageLimit,
                excludedIds: new Set(),
                projectId,
                projectVersionId: versionId || undefined,
                filters: validatedFilters,
              });
            }}
          />
          <CallLogsGrid
            module="project"
            id={projectId}
            enabled={!!projectId}
            cellHeight="Short"
            params={{
              project_id: projectId,
              filters: JSON.stringify(
                objectCamelToSnake(validatedFilters || []),
              ),
            }}
            onSelectionChanged={(traceIds) => {
              onSetSelection(traceIds);
            }}
            onSelectionMeta={setSimCallMeta}
          />
        </Box>
      )}

      {/* Standard trace AG Grid — non-voice projects */}
      {canShowGrid && !isResolvingProjectKind && !isVoiceProject && (
        <Box
          sx={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <SelectAllBanner
            visible={
              !!pageSelectAllMeta &&
              pageSelectAllMeta.totalCount > pageSelectAllMeta.visibleCount
            }
            visibleCount={pageSelectAllMeta?.visibleCount || 0}
            totalMatching={
              pageSelectAllMeta
                ? Math.max(
                    pageSelectAllMeta.totalCount -
                      pageSelectAllMeta.excludedIds.size,
                    0,
                  )
                : 0
            }
            noun="trace"
            onSelectAll={commitFilterModeSelectAll}
          />
          <Box sx={{ flex: 1, position: "relative" }}>
            <GridLoadingOverlay open={isGridLoading} />
            <AgGridReact
              ref={gridRef}
              className="clean-data-table"
              theme={agTheme}
              rowHeight={40}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              rowSelection={{ mode: "multiRow" }}
              pagination={false}
              cacheBlockSize={TRACE_ROWS_LIMIT}
              maxBlocksInCache={3}
              rowBuffer={3}
              suppressServerSideFullWidthLoadingRow
              serverSideInitialRowCount={10}
              rowModelType="serverSide"
              onGridReady={onGridReady}
              onSelectionChanged={onSelectionChanged}
              getRowId={(d) => d?.data?.trace_id ?? d?.data?.traceId}
              animateRows={false}
              blockLoadDebounceMillis={300}
            />
          </Box>
          <StatusBar api={gridApi} />
        </Box>
      )}
    </Box>
  );
}

TraceSelector.propTypes = {
  onSetSelection: PropTypes.func.isRequired,
  onSelectAll: PropTypes.func.isRequired,
  onVoiceProjectChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Span Selector – Same AG Grid as span view with server-side row model
// ---------------------------------------------------------------------------
function SpanSelector({ onSetSelection, onSelectAll }) {
  const [projectId, setProjectId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [columns, setColumns] = useState([]);
  const [filters, setFilters] = useState([
    { ...traceDefaultFilterBase, id: getRandomId() },
  ]);
  const [dateFilter, setDateFilter] = useState(() => ({
    dateFilter: dateFilterForOption("7D"),
    dateOption: "7D",
  }));
  const [, setFilterDefinition] = useState([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);
  const [gridApi, setGridApi] = useState(null);
  const [isGridLoading, setIsGridLoading] = useState(false);
  const gridRef = useRef(null);
  const filterButtonRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const filtersRef = useRef([]);

  const {
    data: projects,
    isLoading: isProjectsLoading,
    isFetching: isProjectsFetching,
  } = useQuery({
    queryKey: ["projects-list-all-for-spans"],
    queryFn: () => axios.get(endpoints.project.listProjects()),
    select: (d) => d.data?.result?.projects || [],
    staleTime: 1000 * 60 * 5,
  });

  const selectedProject = useMemo(
    () => (projects || []).find((p) => p.id === projectId),
    [projects, projectId],
  );
  const isPrototype = selectedProject?.trace_type === "experiment";

  // Fetch versions for prototype projects
  const {
    data: versions,
    isLoading: isVersionsLoading,
    isFetching: isVersionsFetching,
  } = useQuery({
    queryKey: ["project-versions-dropdown-spans", projectId],
    queryFn: () =>
      axios.get(endpoints.project.runListSearch(), {
        params: { project_id: projectId, page_number: 0, page_size: 200 },
      }),
    select: (d) => d.data?.result?.project_version_ids || [],
    enabled: !!projectId && isPrototype,
    staleTime: 1000 * 60 * 2,
  });

  // TraceFilterPanel's output is already well-formed; skip the Zod
  // round-trip (same rationale as TraceSelector above — the legacy
  // AllowedOperators enum strips `in` / `not_in` and any col_type from
  // panels wired after the schema was last updated, causing repeated
  // applies to silently drop filters).
  const validatedMainFilters = useMemo(() => {
    return filters.filter((f) => f?.columnId);
  }, [filters]);

  const validatedFilters = useMemo(() => {
    const range = dateFilter?.dateFilter;
    if (!range || !range[0] || !range[1]) return validatedMainFilters;
    return [
      ...validatedMainFilters,
      {
        columnId: "created_at",
        filterConfig: {
          filterType: "datetime",
          filterOp: "between",
          filterValue: [
            new Date(range[0]).toISOString(),
            new Date(range[1]).toISOString(),
          ],
        },
      },
    ];
  }, [validatedMainFilters, dateFilter]);

  // Keep filtersRef in sync
  useEffect(() => {
    filtersRef.current = validatedFilters;
  }, [validatedFilters]);

  // Server-side datasource (same pattern as SpanGrid)
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        try {
          const { request } = params;
          const pageSize = request.endRow - request.startRow;
          const pageNumber = Math.floor(request.startRow / pageSize);

          const apiParams = {
            project_id: projectId,
            page_number: pageNumber,
            page_size: SPAN_ROWS_LIMIT,
            filters: JSON.stringify(objectCamelToSnake(filtersRef.current)),
          };
          if (versionId) {
            apiParams.project_version_id = versionId;
          }

          setIsGridLoading(true);
          const results = await axios.get(
            endpoints.project.getSpansForObserveProject(),
            { params: apiParams },
          );
          const res = results?.data?.result;

          // Update columns from response config
          const newCols = res?.config?.map((o) => ({
            ...o,
            id: o.id,
          }));
          if (newCols) {
            setColumns((prev) => (isEqual(prev, newCols) ? prev : newCols));
          }

          const totalRows = res?.metadata?.total_rows;
          const ctx = params.api.getGridOption("context") || {};
          params.api.setGridOption("context", {
            ...ctx,
            totalRowCount: totalRows,
          });
          params.success({
            rowData: res?.table,
            rowCount: totalRows,
          });
        } catch {
          params.fail();
        } finally {
          setIsGridLoading(false);
        }
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId, versionId, validatedFilters],
  );

  // Build column defs from server config (reuse getTraceListColumnDefs — same renderers)
  const columnDefs = useMemo(() => {
    if (!columns || columns.length === 0) {
      return SPAN_DEFAULT_COLUMNS;
    }
    return columns
      .filter((col) => !col.groupBy || col.groupBy === "")
      .map((col) => getTraceListColumnDefs(col));
  }, [columns]);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      flex: 1,
      minWidth: 200,
      cellStyle: {
        padding: 0,
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
      suppressSizeToFit: false,
      sortable: false,
    }),
    [],
  );

  // Update filter definition when columns change
  useEffect(() => {
    if (columns.length > 0) {
      const def = generateSpanObserveFilterDefinition(columns, [], null);
      setFilterDefinition(def);
    }
  }, [columns]);

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
      if (projectId) {
        params.api.setGridOption("serverSideDatasource", dataSource);
      }
    },
    [projectId, dataSource],
  );

  // Refresh datasource when project or filters change
  useEffect(() => {
    if (gridApi && projectId) {
      gridApi.setGridOption("serverSideDatasource", dataSource);
    }
  }, [dataSource, gridApi, projectId]);

  // Opt-in for cross-page select-all — same pattern as
  // TraceSelector above (mirrors LLMTracingView's span tab, Phase 5).
  const [pageSelectAllMeta, setPageSelectAllMeta] = useState(null);
  const onSelectionChanged = useCallback(
    (event) => {
      const selectionState = event.api.getServerSideSelectionState();

      if (selectionState.selectAll) {
        const excludedIds = new Set(selectionState.toggledNodes || []);
        const totalCount =
          (event.api.getGridOption("context") || {}).totalRowCount ?? 0;
        const visibleRowIds = [];
        const rendered = event.api.getRenderedNodes?.() || [];
        rendered.forEach((node) => {
          const rowId = node?.data?.span_id ?? node?.data?.spanId ?? node?.id;
          if (rowId && !excludedIds.has(rowId)) visibleRowIds.push(rowId);
        });
        onSetSelection(visibleRowIds);
        setPageSelectAllMeta({
          totalCount,
          excludedIds,
          visibleCount: visibleRowIds.length,
        });
      } else {
        const ids = selectionState.toggledNodes || [];
        onSetSelection(ids);
        setPageSelectAllMeta(null);
      }
    },
    [onSetSelection],
  );

  const commitFilterModeSelectAll = useCallback(() => {
    if (!pageSelectAllMeta) return;
    onSelectAll({
      totalCount: pageSelectAllMeta.totalCount,
      excludedIds: pageSelectAllMeta.excludedIds,
      projectId,
      projectVersionId: versionId || undefined,
      filters: filtersRef.current,
    });
    setPageSelectAllMeta(null);
  }, [pageSelectAllMeta, onSelectAll, projectId, versionId]);

  const isFilterApplied = useMemo(
    () => filters.some((f) => f.columnId),
    [filters],
  );

  const handleProjectChange = (e) => {
    setProjectId(e.target.value);
    setVersionId("");
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterAnchorEl(null);
    setFilterOpen(false);
  };

  const handleVersionChange = (e) => {
    setVersionId(e.target.value);
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterAnchorEl(null);
    setFilterOpen(false);
  };

  // For prototype projects, require a version to be selected before showing grid
  const canShowGrid = projectId && (!isPrototype || versionId);
  const isProjectListLoading =
    isProjectsLoading || (isProjectsFetching && !projects);
  const isVersionListLoading =
    isVersionsLoading || (isVersionsFetching && !versions);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      {/* Project picker + version picker + filter button */}
      <Box
        sx={{
          py: 2,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        <Autocomplete
          size="small"
          loading={isProjectListLoading}
          loadingText="Loading projects..."
          options={projects || []}
          getOptionLabel={(p) => p?.name || ""}
          value={(projects || []).find((p) => p.id === projectId) || null}
          onChange={(_, newValue) =>
            handleProjectChange({
              target: { value: newValue?.id || "" },
            })
          }
          isOptionEqualToValue={(opt, val) => opt?.id === val?.id}
          renderOption={(props, option) => (
            <li {...props} key={option.id}>
              {option.name}
              {option.trace_type === "experiment" && (
                <Chip
                  label="Prototype"
                  size="small"
                  sx={{ ml: 1, height: 20, fontSize: 10 }}
                />
              )}
            </li>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Project"
              placeholder="Choose a project"
              required
              helperText={
                !projectId ? "Required. Select a project to continue." : " "
              }
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {isProjectListLoading && (
                      <CircularProgress size={16} sx={{ mr: 1 }} />
                    )}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 220, flex: "1 1 280px" }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 180, flex: "1 1 220px" }}
            required
            InputProps={{
              endAdornment: (
                <FieldLoadingAdornment loading={isVersionListLoading} />
              ),
            }}
            helperText={!versionId ? "Required for prototype projects." : " "}
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              {isVersionListLoading
                ? "Loading versions..."
                : "Choose a version"}
            </MenuItem>
            {isVersionListLoading && (
              <MenuItem disabled>
                <CircularProgress size={14} sx={{ mr: 1 }} />
                Loading versions...
              </MenuItem>
            )}
            {(versions || []).map((v) => (
              <MenuItem key={v.id} value={v.id}>
                {v.name}
              </MenuItem>
            ))}
          </TextField>
        )}

        {canShowGrid && (
          <>
            <Box sx={{ flex: 1 }} />
            <DateRangePill
              dateFilter={dateFilter}
              setDateFilter={setDateFilter}
            />
            <IconButton
              ref={filterButtonRef}
              size="small"
              onClick={() => {
                setFilterAnchorEl(filterButtonRef.current);
                setFilterOpen((v) => !v);
              }}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: (theme) =>
                  isFilterApplied
                    ? alpha(theme.palette.primary.main, 0.12)
                    : "transparent",
              }}
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_filter.svg"
                sx={{
                  width: 16,
                  height: 16,
                  color: isFilterApplied ? "primary.main" : "text.primary",
                }}
              />
            </IconButton>
          </>
        )}
      </Box>

      {canShowGrid && (
        <TraceFilterPanel
          anchorEl={filterAnchorEl || filterButtonRef.current}
          open={filterOpen}
          onClose={() => setFilterOpen(false)}
          projectId={projectId}
          source="traces"
          currentFilters={validatedMainFilters
            .filter((f) => f?.columnId)
            .map(apiFilterToPanel)}
          onApply={(newPanelFilters) => {
            const apiNext = (newPanelFilters || [])
              .map(panelFilterToApi)
              .filter(apiFilterHasValue);
            setFilters(
              apiNext.length
                ? apiNext.map((f) => ({ ...f, id: getRandomId() }))
                : [{ ...traceDefaultFilterBase, id: getRandomId() }],
            );
          }}
        />
      )}

      {canShowGrid && (
        <FilterChips
          extraFilters={(objectCamelToSnake(validatedMainFilters) || []).filter(
            (f) => f?.column_id && f.column_id !== "created_at",
          )}
          onAddFilter={(anchorEl) => {
            setFilterAnchorEl(anchorEl || filterButtonRef.current);
            setFilterOpen(true);
          }}
          onChipClick={(_idx, anchorEl) => {
            setFilterAnchorEl(anchorEl || filterButtonRef.current);
            setFilterOpen(true);
          }}
          onRemoveFilter={(idx) => {
            setFilterAnchorEl(null);
            const snakeChips = (
              objectCamelToSnake(validatedMainFilters) || []
            ).filter((f) => f?.column_id && f.column_id !== "created_at");
            const target = snakeChips[idx];
            if (!target) return;
            setFilters((prev) =>
              prev.filter((f) => {
                const colMatches = f?.columnId === target.column_id;
                const opMatches =
                  f?.filterConfig?.filterOp ===
                  target?.filter_config?.filter_op;
                return !(colMatches && opMatches);
              }),
            );
          }}
          onClearAll={() => {
            setFilterAnchorEl(null);
            setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
            setFilterOpen(false);
          }}
        />
      )}

      {/* Empty state */}
      {!canShowGrid && (
        <SelectorEmptyState
          loading={!projectId ? isProjectListLoading : isVersionListLoading}
          loadingLabel={
            !projectId ? "Loading projects..." : "Loading versions..."
          }
          title={!projectId ? "Select a project" : "Select a version"}
          description={
            !projectId
              ? "Choose a project from the dropdown above to load its spans."
              : "Choose a version from the dropdown above to load spans."
          }
          requiredLabel={!projectId ? "Project" : "Version"}
        />
      )}

      {/* AG Grid – same as span view */}
      {canShowGrid && (
        <Box
          sx={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <SelectAllBanner
            visible={
              !!pageSelectAllMeta &&
              pageSelectAllMeta.totalCount > pageSelectAllMeta.visibleCount
            }
            visibleCount={pageSelectAllMeta?.visibleCount || 0}
            totalMatching={
              pageSelectAllMeta
                ? Math.max(
                    pageSelectAllMeta.totalCount -
                      pageSelectAllMeta.excludedIds.size,
                    0,
                  )
                : 0
            }
            noun="span"
            onSelectAll={commitFilterModeSelectAll}
          />
          <Box sx={{ flex: 1, position: "relative" }}>
            <GridLoadingOverlay open={isGridLoading} />
            <AgGridReact
              ref={gridRef}
              className="clean-data-table"
              theme={agTheme}
              rowHeight={40}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              rowSelection={{ mode: "multiRow" }}
              pagination={false}
              cacheBlockSize={SPAN_ROWS_LIMIT}
              maxBlocksInCache={3}
              rowBuffer={3}
              suppressServerSideFullWidthLoadingRow
              serverSideInitialRowCount={10}
              rowModelType="serverSide"
              onGridReady={onGridReady}
              onSelectionChanged={onSelectionChanged}
              getRowId={(d) => d?.data?.span_id ?? d?.data?.spanId}
              animateRows={false}
              blockLoadDebounceMillis={300}
            />
          </Box>
          <StatusBar api={gridApi} />
        </Box>
      )}
    </Box>
  );
}

SpanSelector.propTypes = {
  onSetSelection: PropTypes.func.isRequired,
  onSelectAll: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Session Selector – Same AG Grid as sessions view with server-side row model
// ---------------------------------------------------------------------------
const SESSION_ROWS_LIMIT = 30;

function SessionSelector({ onSetSelection }) {
  const [projectId, setProjectId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [columns, setColumns] = useState([]);
  const [filters, setFilters] = useState([
    { ...sessionDefaultFilterBase, id: getRandomId() },
  ]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [gridApi, setGridApi] = useState(null);
  const [isGridLoading, setIsGridLoading] = useState(false);
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const filtersRef = useRef([]);

  const {
    data: projects,
    isLoading: isProjectsLoading,
    isFetching: isProjectsFetching,
  } = useQuery({
    queryKey: ["projects-list-all-for-sessions"],
    queryFn: () => axios.get(endpoints.project.listProjects()),
    select: (d) => d.data?.result?.projects || [],
    staleTime: 1000 * 60 * 5,
  });

  const selectedProject = useMemo(
    () => (projects || []).find((p) => p.id === projectId),
    [projects, projectId],
  );
  const isPrototype = selectedProject?.trace_type === "experiment";

  // Fetch versions for prototype projects
  const {
    data: versions,
    isLoading: isVersionsLoading,
    isFetching: isVersionsFetching,
  } = useQuery({
    queryKey: ["project-versions-dropdown-sessions", projectId],
    queryFn: () =>
      axios.get(endpoints.project.runListSearch(), {
        params: { project_id: projectId, page_number: 0, page_size: 200 },
      }),
    select: (d) => d.data?.result?.project_version_ids || [],
    enabled: !!projectId && isPrototype,
    staleTime: 1000 * 60 * 2,
  });

  // Build validated filters for API calls
  const validatedFilters = useMemo(() => {
    return filters.filter((f) => f.columnId && f.filterConfig?.filterValue);
  }, [filters]);

  // Keep filtersRef in sync
  useEffect(() => {
    filtersRef.current = validatedFilters;
  }, [validatedFilters]);

  // Server-side datasource (same pattern as Session-grid)
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        try {
          const { request } = params;
          const pageNumber = Math.floor(request.startRow / 10);

          setIsGridLoading(true);
          const results = await axios.get(
            endpoints.project.projectSessionList(),
            {
              params: {
                project_id: projectId,
                page_number: pageNumber,
                page_size: SESSION_ROWS_LIMIT,
                sort_params: JSON.stringify(
                  request?.sortModel?.map(({ colId, sort }) => ({
                    column_id: colId,
                    direction: sort,
                  })),
                ),
                filters: JSON.stringify(objectCamelToSnake(filtersRef.current)),
              },
            },
          );
          const res = results?.data?.result;

          // Update columns from response config
          const newCols = res?.config?.map((o) => ({
            ...o,
            id: o.id,
          }));
          if (newCols) {
            setColumns((prev) => (isEqual(prev, newCols) ? prev : newCols));
          }

          const totalRows = res?.metadata?.total_rows;
          const ctx = params.api.getGridOption("context") || {};
          params.api.setGridOption("context", {
            ...ctx,
            totalRowCount: totalRows,
          });
          params.success({
            rowData: res?.table,
            rowCount: totalRows,
          });
        } catch {
          params.fail();
        } finally {
          setIsGridLoading(false);
        }
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId, validatedFilters],
  );

  // Build column defs from server config (same as Session-grid)
  const columnDefs = useMemo(() => {
    if (!columns || columns.length === 0) {
      return [
        {
          field: "session_id",
          headerName: "Session ID",
          flex: 1,
          minWidth: 200,
        },
        {
          field: "firstMessage",
          headerName: "First Message",
          flex: 1,
          minWidth: 200,
        },
        { field: "duration", headerName: "Duration", flex: 1, minWidth: 200 },
        {
          field: "startTime",
          headerName: "Start Time",
          flex: 1,
          minWidth: 200,
        },
        {
          field: "totalCost",
          headerName: "Total Cost",
          flex: 1,
          minWidth: 200,
        },
      ];
    }
    return columns.map((col) => getSessionListColumnDef(col));
  }, [columns]);

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

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
      if (projectId) {
        params.api.setGridOption("serverSideDatasource", dataSource);
      }
    },
    [projectId, dataSource],
  );

  // Refresh datasource when project or filters change
  useEffect(() => {
    if (gridApi && projectId) {
      gridApi.setGridOption("serverSideDatasource", dataSource);
    }
  }, [dataSource, gridApi, projectId]);

  // Handle row selection
  const onSelectionChanged = useCallback(
    (event) => {
      const ids = [];
      event.api.forEachNode((node) => {
        if (node.isSelected() && node.data?.session_id) {
          ids.push(node.data.session_id);
        }
      });
      onSetSelection(ids);
    },
    [onSetSelection],
  );

  const isFilterApplied = useMemo(
    () => filters.some((f) => f.columnId),
    [filters],
  );

  const handleProjectChange = (e) => {
    setProjectId(e.target.value);
    setVersionId("");
    setColumns([]);
    setFilters([{ ...sessionDefaultFilterBase, id: getRandomId() }]);
    setFilterOpen(false);
  };

  const handleVersionChange = (e) => {
    setVersionId(e.target.value);
    setColumns([]);
    setFilters([{ ...sessionDefaultFilterBase, id: getRandomId() }]);
    setFilterOpen(false);
  };

  // For prototype projects, require a version to be selected before showing grid
  const canShowGrid = projectId && (!isPrototype || versionId);
  const isProjectListLoading =
    isProjectsLoading || (isProjectsFetching && !projects);
  const isVersionListLoading =
    isVersionsLoading || (isVersionsFetching && !versions);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      {/* Project picker + version picker + filter button */}
      <Box
        sx={{
          py: 2,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        <Autocomplete
          size="small"
          loading={isProjectListLoading}
          loadingText="Loading projects..."
          options={projects || []}
          getOptionLabel={(p) => p?.name || ""}
          value={(projects || []).find((p) => p.id === projectId) || null}
          onChange={(_, newValue) =>
            handleProjectChange({
              target: { value: newValue?.id || "" },
            })
          }
          isOptionEqualToValue={(opt, val) => opt?.id === val?.id}
          renderOption={(props, option) => (
            <li {...props} key={option.id}>
              {option.name}
              {option.trace_type === "experiment" && (
                <Chip
                  label="Prototype"
                  size="small"
                  sx={{ ml: 1, height: 20, fontSize: 10 }}
                />
              )}
            </li>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Project"
              placeholder="Choose a project"
              required
              helperText={
                !projectId ? "Required. Select a project to continue." : " "
              }
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {isProjectListLoading && (
                      <CircularProgress size={16} sx={{ mr: 1 }} />
                    )}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 220, flex: "1 1 280px" }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 180, flex: "1 1 220px" }}
            required
            InputProps={{
              endAdornment: (
                <FieldLoadingAdornment loading={isVersionListLoading} />
              ),
            }}
            helperText={!versionId ? "Required for prototype projects." : " "}
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              {isVersionListLoading
                ? "Loading versions..."
                : "Choose a version"}
            </MenuItem>
            {isVersionListLoading && (
              <MenuItem disabled>
                <CircularProgress size={14} sx={{ mr: 1 }} />
                Loading versions...
              </MenuItem>
            )}
            {(versions || []).map((v) => (
              <MenuItem key={v.id} value={v.id}>
                {v.name}
              </MenuItem>
            ))}
          </TextField>
        )}

        {canShowGrid && (
          <>
            <Box sx={{ flex: 1 }} />
            <IconButton
              size="small"
              onClick={() => setFilterOpen((v) => !v)}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: (theme) =>
                  isFilterApplied
                    ? alpha(theme.palette.primary.main, 0.12)
                    : "transparent",
              }}
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_filter.svg"
                sx={{
                  width: 16,
                  height: 16,
                  color: isFilterApplied ? "primary.main" : "text.primary",
                }}
              />
            </IconButton>
          </>
        )}
      </Box>

      {/* Filter box – same filter definitions as sessions view */}
      {canShowGrid && filterOpen && (
        <Box sx={{ px: 1.5, pb: 1, flexShrink: 0 }}>
          <Box
            sx={{
              p: 1.5,
            }}
          >
            <LLMFilterBox
              filters={filters}
              setFilters={setFilters}
              filterDefinition={sessionFilterDefinition}
              setFilterDefinition={() => {}}
              defaultFilter={sessionDefaultFilterBase}
              resetFiltersAndClose={() => {
                setFilters([
                  { ...sessionDefaultFilterBase, id: getRandomId() },
                ]);
                setFilterOpen(false);
              }}
            />
          </Box>
        </Box>
      )}

      {/* Empty state */}
      {!canShowGrid && (
        <SelectorEmptyState
          loading={!projectId ? isProjectListLoading : isVersionListLoading}
          loadingLabel={
            !projectId ? "Loading projects..." : "Loading versions..."
          }
          title={!projectId ? "Select a project" : "Select a version"}
          description={
            !projectId
              ? "Choose a project from the dropdown above to load its sessions."
              : "Choose a version from the dropdown above to load sessions."
          }
          requiredLabel={!projectId ? "Project" : "Version"}
        />
      )}

      {/* AG Grid – same as sessions view */}
      {canShowGrid && (
        <Box
          sx={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box sx={{ flex: 1, position: "relative" }}>
            <GridLoadingOverlay open={isGridLoading} />
            <AgGridReact
              ref={gridRef}
              className="clean-data-table"
              theme={agTheme}
              rowHeight={50}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              rowSelection={{ mode: "multiRow" }}
              pagination={false}
              cacheBlockSize={SESSION_ROWS_LIMIT}
              maxBlocksInCache={3}
              suppressServerSideFullWidthLoadingRow
              serverSideInitialRowCount={5}
              rowModelType="serverSide"
              onGridReady={onGridReady}
              onSelectionChanged={onSelectionChanged}
              getRowId={({ data }) => data.session_id}
              suppressRowClickSelection
              animateRows={false}
              blockLoadDebounceMillis={300}
            />
          </Box>
          <StatusBar api={gridApi} />
        </Box>
      )}
    </Box>
  );
}

SessionSelector.propTypes = {
  onSetSelection: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Simulation Selector – Test → Execution Run → Call Executions
// ---------------------------------------------------------------------------
const SIMULATION_ROWS_LIMIT = 20;

function getNestedValue(source, key) {
  if (!source || !key) return undefined;
  if (!key.includes(".")) return source[key];
  return key.split(".").reduce((current, part) => current?.[part], source);
}

function getCallValue(data, keys) {
  const latencyMetrics =
    data?.customer_latency_metrics ||
    data?.customerLatencyMetrics ||
    data?.call_details?.customer_latency_metrics ||
    data?.callDetails?.customerLatencyMetrics;
  const sources = [
    data,
    data?.call_details,
    data?.callDetails,
    data?.call_metadata,
    data?.callMetadata,
    data?.conversation_metrics_data,
    data?.conversationMetricsData,
    latencyMetrics,
    latencyMetrics?.systemMetrics,
    latencyMetrics?.system_metrics,
    latencyMetrics?.detailed_data,
    latencyMetrics?.detailedData,
  ].filter(Boolean);

  for (const key of keys) {
    for (const source of sources) {
      const value = getNestedValue(source, key);
      if (value !== undefined && value !== null && value !== "") return value;
    }
  }
  return null;
}

function formatNumber(value, options = {}) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value == null ? "-" : String(value);
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: options.maximumFractionDigits ?? 2,
    minimumFractionDigits: options.minimumFractionDigits ?? 0,
  }).format(numeric);
}

function formatSeconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value == null ? "-" : String(value);
  if (numeric >= 60) {
    const totalSeconds = Math.round(numeric);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }
  return `${formatNumber(numeric, { maximumFractionDigits: 2 })}s`;
}

function formatMilliseconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value == null ? "-" : String(value);
  if (numeric >= 1000) {
    return `${formatNumber(numeric / 1000, { maximumFractionDigits: 2 })}s`;
  }
  return `${Math.round(numeric)}ms`;
}

function formatCurrencyCents(value) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value == null ? "-" : String(value);
  return `$${formatNumber(numeric / 100, {
    minimumFractionDigits: numeric ? 2 : 0,
    maximumFractionDigits: 4,
  })}`;
}

function formatCurrencyDollars(value) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value == null ? "-" : String(value);
  return `$${formatNumber(numeric, {
    minimumFractionDigits: numeric ? 2 : 0,
    maximumFractionDigits: 4,
  })}`;
}

function formatGenericSimulationValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value))
    return value.map(formatGenericSimulationValue).join(", ");
  if (typeof value === "object") {
    if ("value" in value) return formatGenericSimulationValue(value.value);
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function formatLatency(data) {
  return formatMilliseconds(
    getCallValue(data, [
      "avg_agent_latency",
      "avg_agent_latency_ms",
      "avgAgentLatencyMs",
      "avg_latency_ms",
      "average_latency_ms",
      "latency",
      "latency_ms",
      "turnLatencyAverage",
      "turn_latency_average",
    ]),
  );
}

function formatAgentTalkPercentage(data) {
  const direct = getCallValue(data, [
    "agent_talk_percentage",
    "agentTalkPercentage",
    "bot_pct",
    "botPct",
  ]);
  if (direct !== null) {
    return `${formatNumber(direct, { maximumFractionDigits: 1 })}%`;
  }

  const ratio = getCallValue(data, ["talk_ratio", "talkRatio"]);
  if (ratio === null) return "-";

  if (ratio && typeof ratio === "object") {
    const objectValue = ratio.bot_pct ?? ratio.botPct ?? ratio.agent_pct;
    return objectValue == null
      ? "-"
      : `${formatNumber(objectValue, { maximumFractionDigits: 1 })}%`;
  }

  const numericRatio = Number(ratio);
  if (Number.isFinite(numericRatio) && numericRatio >= 0) {
    const denominator = numericRatio + 1;
    if (denominator > 0) {
      return `${formatNumber((numericRatio / denominator) * 100, {
        maximumFractionDigits: 1,
      })}%`;
    }
  }

  return "-";
}

function formatCost(data) {
  const cents = getCallValue(data, [
    "customer_cost_cents",
    "customerCostCents",
    "cost_cents",
    "costCents",
    "total_cost_cents",
    "totalCostCents",
  ]);
  if (cents !== null) return formatCurrencyCents(cents);

  const dollars = getCallValue(data, [
    "customer_cost_breakdown.total",
    "customerCostBreakdown.total",
    "cost_breakdown.total",
    "costBreakdown.total",
    "total_cost",
    "totalCost",
    "cost",
  ]);
  return dollars !== null ? formatCurrencyDollars(dollars) : "-";
}

function SimulationTextCellRenderer({ value, valueFormatted }) {
  const displayValue = valueFormatted ?? value;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" noWrap title={String(displayValue ?? "")}>
        {formatGenericSimulationValue(displayValue)}
      </Typography>
    </Box>
  );
}

SimulationTextCellRenderer.propTypes = {
  value: PropTypes.any,
  valueFormatted: PropTypes.any,
};

function CallStatusCellRenderer({ data }) {
  if (!data) return null;
  const details = data.call_details || data;
  const status = details?.status || data.status;
  if (!status) return null;
  const colorMap = {
    completed: "success",
    Completed: "success",
    failed: "error",
    Failed: "error",
    in_progress: "info",
    Running: "info",
    pending: "warning",
    Pending: "warning",
  };
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Chip
        label={status}
        size="small"
        color={colorMap[status] || "default"}
        variant="outlined"
        sx={{ height: 24, fontSize: 12, textTransform: "capitalize" }}
      />
    </Box>
  );
}

CallStatusCellRenderer.propTypes = {
  data: PropTypes.object,
};

function CallDetailSimpleCellRenderer({ data }) {
  if (!data) return null;
  const details = data.call_details || data;
  const name =
    details.customer_name ||
    details.scenario ||
    details.call_summary ||
    details.phone_number ||
    "";
  const type =
    details.simulation_call_type || details.call_type || data.call_type || "";
  const startTime =
    details.start_time ||
    details.started_at ||
    data.timestamp ||
    data.started_at;
  const timeStr = startTime
    ? new Date(startTime).toLocaleString("en-US", {
        month: "2-digit",
        day: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        height: "100%",
        gap: 0.25,
      }}
    >
      <Typography variant="body2" noWrap fontWeight={500}>
        {name || type || "Call"}
      </Typography>
      {(timeStr || type) && (
        <Typography variant="caption" color="text.secondary">
          {[type, timeStr].filter(Boolean).join(" - ")}
        </Typography>
      )}
    </Box>
  );
}

CallDetailSimpleCellRenderer.propTypes = {
  data: PropTypes.object,
};

function formatExecutionRunLabel(run) {
  const scenario = run.scenarios || "No scenarios";
  const startedAt = run.start_time;
  const time = startedAt
    ? new Date(startedAt).toLocaleString("en-US", {
        month: "2-digit",
        day: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  const status = run.status || "";
  return `${scenario}${time ? ` - ${time}` : ""}${status ? ` (${status})` : ""}`;
}

const SIMULATION_STATIC_COLUMNS = [
  {
    id: "call_details",
    headerName: "Call Details",
    flex: 2,
    minWidth: 260,
    cellRenderer: CallDetailSimpleCellRenderer,
  },
  {
    id: "status",
    headerName: "Status",
    flex: 0.8,
    minWidth: 120,
    valueGetter: (params) => getCallValue(params.data, ["status"]),
    cellRenderer: CallStatusCellRenderer,
  },
  {
    id: "timestamp",
    headerName: "Timestamp",
    flex: 1,
    minWidth: 170,
    valueGetter: (params) =>
      getCallValue(params.data, ["timestamp", "started_at", "start_time"]),
    valueFormatter: (params) => {
      if (!params.value) return "-";
      try {
        return new Date(params.value).toLocaleString("en-US", {
          month: "2-digit",
          day: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
      } catch {
        return "-";
      }
    },
  },
  {
    id: "duration",
    headerName: "Duration",
    flex: 0.7,
    minWidth: 115,
    valueGetter: (params) =>
      formatSeconds(
        getCallValue(params.data, ["duration", "duration_seconds"]),
      ),
  },
  {
    id: "latency",
    headerName: "Latency",
    flex: 0.7,
    minWidth: 115,
    valueGetter: (params) => formatLatency(params.data),
  },
  {
    id: "turn_count",
    headerName: "Turn Count",
    flex: 0.7,
    minWidth: 115,
    valueGetter: (params) =>
      formatGenericSimulationValue(getCallValue(params.data, ["turn_count"])),
  },
  {
    id: "agent_talk_percentage",
    headerName: "Agent Talk (%)",
    flex: 0.8,
    minWidth: 135,
    valueGetter: (params) => formatAgentTalkPercentage(params.data),
  },
  {
    id: "cost_cents",
    headerName: "Cost",
    flex: 0.7,
    minWidth: 105,
    valueGetter: (params) => formatCost(params.data),
  },
  {
    id: "ended_reason",
    headerName: "Ended Reason",
    flex: 1,
    minWidth: 150,
    valueGetter: (params) =>
      formatGenericSimulationValue(getCallValue(params.data, ["ended_reason"])),
  },
  {
    id: "call_type",
    headerName: "Type",
    flex: 0.7,
    minWidth: 105,
    valueGetter: (params) =>
      formatGenericSimulationValue(getCallValue(params.data, ["call_type"])),
  },
  {
    id: "overall_score",
    headerName: "Overall Score",
    flex: 0.8,
    minWidth: 130,
    valueGetter: (params) =>
      formatGenericSimulationValue(
        getCallValue(params.data, ["overall_score"]),
      ),
  },
];

const SIMULATION_COLUMN_ALIASES = {
  avg_agent_latency: "latency",
  avg_agent_latency_ms: "latency",
  avgAgentLatencyMs: "latency",
  avg_latency_ms: "latency",
  avgLatencyMs: "latency",
  average_latency_ms: "latency",
  latency_ms: "latency",
  customer_cost_cents: "cost_cents",
  customerCostCents: "cost_cents",
  cost: "cost_cents",
  costCents: "cost_cents",
  total_cost: "cost_cents",
  total_cost_cents: "cost_cents",
  totalCost: "cost_cents",
  totalCostCents: "cost_cents",
  responseTime: "response_time",
  avg_response_time_ms: "response_time",
  average_response_time_ms: "response_time",
  response_time_ms: "response_time",
  responseTimeMs: "response_time",
  response_time_seconds: "response_time",
  responseTimeSeconds: "response_time",
  agentTalkPercentage: "agent_talk_percentage",
};

function normalizeSimulationColumnId(columnId) {
  return SIMULATION_COLUMN_ALIASES[columnId] || columnId;
}

const SIMULATION_HIDDEN_COLUMN_IDS = new Set([
  // Voice observability keeps Response Time hidden, so do not surface it in the
  // Add Items picker even when older execution column orders include aliases.
  "response_time",
]);

function createDynamicSimulationColumn(col) {
  const columnId = col.id;
  const headerName = col.column_name || columnId;

  if (col.type === "scenario_dataset_column") {
    return {
      headerName,
      field: columnId,
      colId: columnId,
      flex: 1,
      minWidth: 160,
      valueGetter: (params) =>
        formatGenericSimulationValue(params.data?.scenario_columns?.[columnId]),
      cellRenderer: SimulationTextCellRenderer,
    };
  }

  if (col.type === "evaluation") {
    return {
      headerName,
      field: columnId,
      colId: columnId,
      flex: 1,
      minWidth: 160,
      valueGetter: (params) =>
        formatGenericSimulationValue(params.data?.eval_metrics?.[columnId]),
      cellRenderer: SimulationTextCellRenderer,
    };
  }

  if (col.type === "tool_evaluation") {
    return {
      headerName,
      field: columnId,
      colId: columnId,
      flex: 1,
      minWidth: 160,
      valueGetter: (params) =>
        formatGenericSimulationValue(
          params.data?.tool_outputs?.[col.column_name],
        ),
      cellRenderer: SimulationTextCellRenderer,
    };
  }

  return {
    headerName,
    field: columnId,
    colId: columnId,
    flex: 1,
    minWidth: 140,
    valueGetter: (params) =>
      formatGenericSimulationValue(getCallValue(params.data, [columnId])),
    cellRenderer: SimulationTextCellRenderer,
  };
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildSimulationSelectorColumnDefs(columnOrder = []) {
  const staticById = new Map(
    SIMULATION_STATIC_COLUMNS.flatMap((column) => [
      [column.id, column],
      [normalizeSimulationColumnId(column.id), column],
    ]),
  );
  const seen = new Set();
  const columns = [];

  const addColumn = (column) => {
    const columnId = column.colId || column.field || column.id;
    const normalizedColumnId = normalizeSimulationColumnId(columnId);
    if (
      !columnId ||
      SIMULATION_HIDDEN_COLUMN_IDS.has(normalizedColumnId) ||
      seen.has(normalizedColumnId)
    )
      return;
    seen.add(normalizedColumnId);
    columns.push({
      cellRenderer: SimulationTextCellRenderer,
      ...column,
      field: column.field || column.id,
      colId: columnId,
    });
  };

  SIMULATION_STATIC_COLUMNS.forEach(addColumn);

  columnOrder.forEach((col) => {
    if (!col?.id) return;
    const columnId = col.id;
    const normalizedColumnId = normalizeSimulationColumnId(columnId);
    if (
      SIMULATION_HIDDEN_COLUMN_IDS.has(normalizedColumnId) ||
      seen.has(normalizedColumnId)
    )
      return;
    const staticColumn =
      staticById.get(columnId) || staticById.get(normalizedColumnId);
    addColumn(
      staticColumn
        ? {
            ...staticColumn,
            headerName: col.column_name || staticColumn.headerName,
          }
        : createDynamicSimulationColumn({ ...col, id: columnId }),
    );
  });

  return columns;
}

function SimulationSelector({ onSetSelection }) {
  const [testId, setTestId] = useState("");
  const [executionRunId, setExecutionRunId] = useState("");
  const [gridApi, setGridApi] = useState(null);
  const [isGridLoading, setIsGridLoading] = useState(false);
  const [simulationColumnOrder, setSimulationColumnOrder] = useState([]);
  const columnOrderSignatureRef = useRef("");
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const queryClient = useQueryClient();

  // 1. Fetch list of tests (infinite)
  const {
    testsList: tests,
    fetchNextPage: fetchNextTestsPage,
    isFetchingNextPage: isFetchingNextTestsPage,
    isFetching: isFetchingTests,
  } = useTestRunsList();

  const handleTestsMenuScroll = useCallback(
    (e) => {
      const el = e.target;
      if (
        el.scrollHeight - el.scrollTop - el.clientHeight < 50 &&
        !isFetchingNextTestsPage
      ) {
        fetchNextTestsPage();
      }
    },
    [isFetchingNextTestsPage, fetchNextTestsPage],
  );

  // 2. Fetch execution runs for selected test
  const {
    data: executionRuns,
    isLoading: isExecutionRunsLoading,
    isFetching: isExecutionRunsFetching,
  } = useQuery({
    queryKey: ["sim-execution-runs-dropdown", testId],
    queryFn: () =>
      axios.get(endpoints.runTests.detailExecutions(testId), {
        params: { page: 1, limit: 100 },
      }),
    select: (d) => d.data?.results || [],
    enabled: !!testId,
    staleTime: 1000 * 60 * 2,
  });

  // 3. Server-side datasource for call executions within selected run
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        try {
          const { request } = params;
          const pageSize = request.endRow - request.startRow;
          const pageNumber = Math.floor(request.startRow / pageSize);

          setIsGridLoading(true);
          const { data } = await queryClient.fetchQuery({
            queryKey: [
              "sim-call-executions",
              executionRunId,
              pageNumber,
              pageSize,
            ],
            queryFn: () =>
              axios.get(endpoints.testExecutions.list(executionRunId), {
                params: { page: pageNumber + 1, limit: pageSize },
              }),
          });

          const rows = data?.results ?? [];
          const totalRows = data?.count ?? rows.length;
          const nextColumnOrder = data?.column_order ?? [];
          const nextSignature = JSON.stringify(
            nextColumnOrder.map((col) => [
              col?.id,
              col?.column_name,
              col?.type,
            ]),
          );
          if (nextSignature !== columnOrderSignatureRef.current) {
            columnOrderSignatureRef.current = nextSignature;
            setSimulationColumnOrder(nextColumnOrder);
          }

          params.success({
            rowData: rows,
            rowCount: totalRows,
          });

          const ctx = params.api.getGridOption("context") || {};
          params.api.setGridOption("context", {
            ...ctx,
            totalRowCount: totalRows,
          });
        } catch {
          params.fail();
        } finally {
          setIsGridLoading(false);
        }
      },
    }),
    [executionRunId, queryClient],
  );

  const columnDefs = useMemo(
    () => buildSimulationSelectorColumnDefs(simulationColumnOrder),
    [simulationColumnOrder],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      sortable: false,
    }),
    [],
  );

  const onGridReady = useCallback(
    (params) => {
      setGridApi(params.api);
      if (executionRunId) {
        params.api.setGridOption("serverSideDatasource", dataSource);
      }
    },
    [executionRunId, dataSource],
  );

  // Refresh datasource when execution run changes
  useEffect(() => {
    if (gridApi && executionRunId) {
      gridApi.setGridOption("serverSideDatasource", dataSource);
    }
  }, [executionRunId, gridApi, dataSource]);

  const onSelectionChanged = useCallback(
    (event) => {
      const ids = [];
      event.api.forEachNode((node) => {
        if (node.isSelected() && node.data?.id) {
          ids.push(node.data.id);
        }
      });
      onSetSelection(ids);
    },
    [onSetSelection],
  );

  const handleTestChange = (e) => {
    setTestId(e.target.value);
    setExecutionRunId("");
    columnOrderSignatureRef.current = "";
    setSimulationColumnOrder([]);
    onSetSelection([]);
  };

  const handleExecutionRunChange = (e) => {
    setExecutionRunId(e.target.value);
    columnOrderSignatureRef.current = "";
    setSimulationColumnOrder([]);
    onSetSelection([]);
  };

  const isTestsLoading = isFetchingTests && tests.length === 0;
  const isExecutionListLoading =
    isExecutionRunsLoading || (isExecutionRunsFetching && !executionRuns);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
      }}
    >
      {/* Test picker + Execution run picker */}
      <Box
        sx={{
          py: 2,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        <TextField
          select
          size="small"
          label="Test"
          value={testId}
          onChange={handleTestChange}
          sx={{ minWidth: 220, flex: "1 1 280px" }}
          required
          InputLabelProps={{ shrink: true }}
          InputProps={{
            endAdornment: <FieldLoadingAdornment loading={isTestsLoading} />,
          }}
          helperText={!testId ? "Required. Select a test to continue." : " "}
          SelectProps={{
            displayEmpty: true,
            renderValue: (v) => {
              if (!v) return "Choose a test";
              const t = tests.find((r) => r.id === v);
              return t?.name || v;
            },
            MenuProps: {
              PaperProps: {
                onScroll: handleTestsMenuScroll,
                style: { maxHeight: 300 },
              },
            },
          }}
        >
          <MenuItem value="" disabled>
            {isTestsLoading ? "Loading tests..." : "Choose a test"}
          </MenuItem>
          {isTestsLoading && (
            <MenuItem disabled>
              <CircularProgress size={14} sx={{ mr: 1 }} />
              Loading tests...
            </MenuItem>
          )}
          {tests.map((t) => (
            <MenuItem key={t.id} value={t.id} sx={{ maxWidth: 300 }}>
              <CustomTooltip
                size="small"
                arrow
                show
                type=""
                title={t.name}
                placement="top"
              >
                <Typography
                  variant="body2"
                  noWrap
                  sx={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    width: "100%",
                  }}
                >
                  {t.name}
                </Typography>
              </CustomTooltip>
            </MenuItem>
          ))}
          {isFetchingNextTestsPage && (
            <MenuItem disabled>
              <CircularProgress size={14} sx={{ mx: "auto" }} />
            </MenuItem>
          )}
        </TextField>

        {testId && (
          <TextField
            select
            size="small"
            label="Execution run"
            value={executionRunId}
            onChange={handleExecutionRunChange}
            sx={{ minWidth: 220, flex: "1 1 320px" }}
            required
            InputLabelProps={{ shrink: true }}
            InputProps={{
              endAdornment: (
                <FieldLoadingAdornment loading={isExecutionListLoading} />
              ),
            }}
            helperText={
              !executionRunId
                ? "Required. Select an execution run to continue."
                : " "
            }
            SelectProps={{
              displayEmpty: true,
              renderValue: (v) => {
                if (!v) return "Choose an execution run";
                const run = (executionRuns || []).find((r) => r.id === v);
                return run ? formatExecutionRunLabel(run) : v;
              },
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              {isExecutionListLoading
                ? "Loading execution runs..."
                : "Choose an execution run"}
            </MenuItem>
            {isExecutionListLoading && (
              <MenuItem disabled>
                <CircularProgress size={14} sx={{ mr: 1 }} />
                Loading execution runs...
              </MenuItem>
            )}
            {(executionRuns || []).map((run) => {
              const label = formatExecutionRunLabel(run);
              return (
                <MenuItem key={run.id} value={run.id} sx={{ maxWidth: 340 }}>
                  <CustomTooltip
                    size="small"
                    arrow
                    show
                    type=""
                    title={label}
                    placement="top"
                  >
                    <Typography
                      variant="body2"
                      noWrap
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        width: "100%",
                      }}
                    >
                      {label}
                    </Typography>
                  </CustomTooltip>
                </MenuItem>
              );
            })}
          </TextField>
        )}
      </Box>

      {/* Empty state */}
      {!testId && (
        <SelectorEmptyState
          loading={isTestsLoading}
          loadingLabel="Loading tests..."
          title="Select a test"
          description="Choose a test from the dropdown above, then select an execution run."
          requiredLabel="Test"
        />
      )}

      {testId && !executionRunId && (
        <SelectorEmptyState
          loading={isExecutionListLoading}
          loadingLabel="Loading execution runs..."
          title="Select an execution run"
          description="Choose an execution run to view its calls and chats."
          requiredLabel="Execution run"
        />
      )}

      {/* AG Grid – call executions within selected run */}
      {executionRunId && (
        <Box
          sx={{
            flex: 1,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Box sx={{ flex: 1, position: "relative" }}>
            <GridLoadingOverlay open={isGridLoading} />
            <AgGridReact
              ref={gridRef}
              className="clean-data-table"
              theme={agTheme}
              rowHeight={56}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              rowSelection={{ mode: "multiRow" }}
              pagination={false}
              cacheBlockSize={SIMULATION_ROWS_LIMIT}
              maxBlocksInCache={3}
              rowBuffer={3}
              suppressServerSideFullWidthLoadingRow
              serverSideInitialRowCount={10}
              rowModelType="serverSide"
              onGridReady={onGridReady}
              onSelectionChanged={onSelectionChanged}
              getRowId={(d) => d?.data?.id}
              animateRows={false}
              blockLoadDebounceMillis={300}
            />
          </Box>
          <StatusBar api={gridApi} />
        </Box>
      )}
    </Box>
  );
}

SimulationSelector.propTypes = {
  onSetSelection: PropTypes.func.isRequired,
};
