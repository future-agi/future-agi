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

// ---------------------------------------------------------------------------
// TraceFilterPanel ↔ API filter converters (mirror ObserveToolbar's inline
// logic). Moved here so the dialog's Trace and Span selectors can mount the
// same popover the main tracing page uses.
// ---------------------------------------------------------------------------
const PANEL_OP_TO_API = {
  is: "equals",
  is_not: "not_equals",
  contains: "contains",
  not_contains: "not_contains",
  equals: "equals",
  equal_to: "equal_to",
  not_equal_to: "not_equal_to",
  greater_than: "greater_than",
  greater_than_or_equal: "greater_than_or_equal",
  less_than: "less_than",
  less_than_or_equal: "less_than_or_equal",
  between: "between",
  not_between: "not_between",
};
const API_OP_TO_PANEL = {
  equals: "is",
  not_equals: "is_not",
  contains: "contains",
  not_contains: "not_contains",
  starts_with: "starts_with",
  // `in` / `not_in` are the multi-value promotion of equals / not_equals
  // that panelFilterToApi emits. Reverse back to `is` / `is_not` so the
  // panel's operator Select finds a matching option (STRING_OPS and
  // CATEGORICAL_OPS don't include `in` — without the reverse mapping the
  // Operator dropdown goes blank on re-open).
  in: "is",
  not_in: "is_not",
};
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
const NUMBER_OPS = new Set([
  "equal_to",
  "not_equal_to",
  "greater_than",
  "greater_than_or_equal",
  "less_than",
  "less_than_or_equal",
  "between",
  "not_between",
]);
const RANGE_OPS = new Set(["between", "not_between"]);

function panelFilterToApi(panel) {
  const baseOp = PANEL_OP_TO_API[panel.operator] || panel.operator;
  let filterOp = baseOp;
  let filterValue = panel.value;
  if (Array.isArray(filterValue)) {
    if (filterValue.length === 1) {
      filterValue = filterValue[0];
    } else if (filterValue.length > 1) {
      if (baseOp === "equals") filterOp = "in";
      else if (baseOp === "not_equals") filterOp = "not_in";
      else filterValue = filterValue.join(",");
    }
  }
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
  const isNumberOp = NUMBER_OPS.has(rawOp);
  const isRange = RANGE_OPS.has(rawOp);
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
  return {
    field: api.columnId,
    fieldName: api.displayName,
    fieldCategory: COL_TYPE_TO_PANEL_CAT[rawColType] || "system",
    fieldType: isNumberOp
      ? "number"
      : filterType === "number"
        ? "number"
        : filterType === "categorical"
          ? "categorical"
          : filterType === "text" && rawColType === "ANNOTATION"
            ? "text"
            : "string",
    operator: isNumberOp ? rawOp : API_OP_TO_PANEL[rawOp] || rawOp,
    value,
  };
}
import { getComplexFilterValidation } from "src/components/ComplexFilter/common";
import {
  getSessionListColumnDef,
  filterDefinition as sessionFilterDefinition,
  defaultFilter as sessionDefaultFilterBase,
} from "src/sections/projects/SessionsView/common";
import "src/styles/clean-data-table.css";
import { fetchRootSpans } from "src/api/project/llm-tracing";
import { VoiceCallsGrid } from "src/components/data-table";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const DATASET_ROWS_LIMIT = 10;
const TRACE_ROWS_LIMIT = 20;
const DEFAULT_MIN_WIDTH = 300;
const DONT_FORMAT_COL_ID_FOR = [
  "Evaluation Metrics",
  "Attribute",
  "Annotation Metrics",
];

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
async function fetchAllTraceIds(projectId, excludedIds, filters, projectVersionId) {
  const serializedFilters = JSON.stringify(
    objectCamelToSnake(filters || []),
  );
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

async function fetchAllSpanIds(projectId, excludedIds, filters, projectVersionId) {
  const serializedFilters = JSON.stringify(
    objectCamelToSnake(filters || []),
  );
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
export default function AddItemsDialog({ open, onClose, queueId }) {
  const [sourceType, setSourceType] = useState(null);
  // Selection can be in two modes:
  // 'manual' – individual IDs tracked in selectedIds
  // 'selectAll' – all rows selected, minus excludedIds tracked in selectAllInfo
  const [selectionMode, setSelectionMode] = useState("manual");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [selectAllInfo, setSelectAllInfo] = useState(null);
  const [isResolving, setIsResolving] = useState(false);
  const { mutate: addItems, isPending } = useAddQueueItems();
  const queryClient = useQueryClient();

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
        const ids = Array.from(selectedIds);
        itemsToAdd = ids.map((id) => ({
          source_type: sourceType,
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
        <SourceTypeSelection onClose={handleClose} onSelect={setSourceType} />
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
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <IconButton size="small" onClick={handleBack}>
                <Iconify icon="eva:arrow-ios-back-fill" width={20} />
              </IconButton>
              <Box>
                <Typography variant="h6">{sourceLabel}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {sourceSubtitle}
                </Typography>
              </Box>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography
                component="a"
                variant="body2"
                href="#"
                sx={{ color: "primary.main", textDecoration: "none" }}
              >
                Learn more
              </Typography>
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
              justifyContent: "flex-end",
              alignItems: "center",
              gap: 1.5,
              flexShrink: 0,
            }}
          >
            <Button
              variant="outlined"
              color="primary"
              onClick={handleClose}
              disabled={isPending || isResolving}
              sx={{ minWidth: 160 }}
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
              sx={{ minWidth: 160 }}
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
};

// ---------------------------------------------------------------------------
// Source Type Selection (Step 1)
// ---------------------------------------------------------------------------
function SourceTypeSelection({ onSelect, onClose }) {
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
function createDataSource(queryClient, datasetId, filtersRef, searchRef) {
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
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(DATASET_GRID_THEME_PARAMS);
  const queryClient = useQueryClient();
  const filtersRef = useRef([]);
  const searchRef = useRef("");

  const { data: datasets } = useQuery({
    queryKey: ["datasets-list-simple"],
    queryFn: () => axios.get("/model-hub/develops/get-datasets-names/"),
    select: (d) => d.data?.result?.datasets || [],
    staleTime: 1000 * 60 * 5,
  });

  // Get column config from page 0
  const { data: tableData } = useQuery(
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
          flexShrink: 0,
        }}
      >
        <TextField
          select
          size="small"
          label="Dataset"
          value={datasetId}
          onChange={handleDatasetChange}
          sx={{ minWidth: 300 }}
          required
          SelectProps={{
            MenuProps: {
              PaperProps: {
                style: { maxHeight: 300, overflowY: "auto" },
              },
            },
          }}
        >
          <MenuItem value="" disabled>
            Choose a dataset
          </MenuItem>
          {(datasets || []).map((ds) => (
            <MenuItem key={ds.datasetId || ds.id} value={ds.datasetId || ds.id}>
              {ds.name}
            </MenuItem>
          ))}
        </TextField>

        {datasetId && (
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
                bgcolor: isFilterApplied ? "primary.lighter" : "transparent",
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
              sx={{ minWidth: 220 }}
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
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            Start by Selecting a Dataset
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Pick a dataset from the dropdown above to load its datapoints.
          </Typography>
        </Box>
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
            <Box sx={{ flex: 1 }}>
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

function TraceSelector({ onSetSelection, onSelectAll }) {
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
  const [filterDefinition, setFilterDefinition] = useState([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [gridApi, setGridApi] = useState(null);
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

  const { data: projects } = useQuery({
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
  const { data: projectDetails } = useGetProjectDetails(projectId, !!projectId);
  const isVoiceProject = projectDetails?.source === PROJECT_SOURCE.SIMULATOR;

  // Fetch versions for prototype projects
  const { data: versions } = useQuery({
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
          const rowId =
            node?.data?.trace_id ?? node?.data?.traceId ?? node?.id;
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
    setFilterOpen(false);
    onSetSelection([]);
  };

  const handleVersionChange = (e) => {
    setVersionId(e.target.value);
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterOpen(false);
  };

  // For prototype projects, require a version to be selected before showing grid
  const canShowGrid = projectId && (!isPrototype || versionId);

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
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 300 }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 220 }}
            required
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              Choose a version
            </MenuItem>
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
              onClick={() => setFilterOpen((v) => !v)}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: isFilterApplied ? "primary.lighter" : "transparent",
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
          anchorEl={filterButtonRef.current}
          open={filterOpen}
          onClose={() => setFilterOpen(false)}
          projectId={projectId}
          isSimulator={isVoiceProject}
          currentFilters={validatedMainFilters
            .filter((f) => f?.columnId)
            .map(apiFilterToPanel)}
          onApply={(newPanelFilters) => {
            const apiNext = (newPanelFilters || []).map(panelFilterToApi);
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
          onAddFilter={() => setFilterOpen(true)}
          onRemoveFilter={(idx) => {
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
            setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
            setFilterOpen(false);
          }}
        />
      )}

      {/* Empty state */}
      {!canShowGrid && (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            {!projectId ? "Start by Selecting a Project" : "Select a Version"}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {!projectId
              ? "Pick a project from the dropdown above to load its traces."
              : "Pick a version from the dropdown above to load traces."}
          </Typography>
        </Box>
      )}

      {/* Voice / simulator projects: use the same CallLogsGrid the main
          LLM Tracing page renders for simulator projects (voice-specific
          columns: Duration, Avg Latency, Turn Count, Talk Ratio, Cost).

          Phase 9 pattern — CallLogsGrid is client-side paginated, so
          clicking the header checkbox only picks the visible page
          (~25 rows). The SelectAllBanner below surfaces the
          cross-page total so the user can opt into filter-mode bulk
          add (same as LLMTracingView's simulator branch). */}
      {canShowGrid && isVoiceProject && (
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
      {canShowGrid && !isVoiceProject && (
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
          <Box sx={{ flex: 1 }}>
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
  const [filterDefinition, setFilterDefinition] = useState([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [gridApi, setGridApi] = useState(null);
  const gridRef = useRef(null);
  const filterButtonRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const filtersRef = useRef([]);

  const { data: projects } = useQuery({
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
  const { data: versions } = useQuery({
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
    setFilterOpen(false);
  };

  const handleVersionChange = (e) => {
    setVersionId(e.target.value);
    setColumns([]);
    setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
    setFilterOpen(false);
  };

  // For prototype projects, require a version to be selected before showing grid
  const canShowGrid = projectId && (!isPrototype || versionId);

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
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 300 }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 220 }}
            required
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              Choose a version
            </MenuItem>
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
              onClick={() => setFilterOpen((v) => !v)}
              sx={{
                border: "1px solid",
                borderColor: isFilterApplied ? "primary.main" : "divider",
                borderRadius: 0.5,
                p: 0.75,
                bgcolor: isFilterApplied ? "primary.lighter" : "transparent",
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
          anchorEl={filterButtonRef.current}
          open={filterOpen}
          onClose={() => setFilterOpen(false)}
          projectId={projectId}
          source="traces"
          currentFilters={validatedMainFilters
            .filter((f) => f?.columnId)
            .map(apiFilterToPanel)}
          onApply={(newPanelFilters) => {
            const apiNext = (newPanelFilters || []).map(panelFilterToApi);
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
          onAddFilter={() => setFilterOpen(true)}
          onRemoveFilter={(idx) => {
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
            setFilters([{ ...traceDefaultFilterBase, id: getRandomId() }]);
            setFilterOpen(false);
          }}
        />
      )}

      {/* Empty state */}
      {!canShowGrid && (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            {!projectId ? "Start by Selecting a Project" : "Select a Version"}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {!projectId
              ? "Pick a project from the dropdown above to load its spans."
              : "Pick a version from the dropdown above to load spans."}
          </Typography>
        </Box>
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
          <Box sx={{ flex: 1 }}>
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
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const filtersRef = useRef([]);

  const { data: projects } = useQuery({
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
  const { data: versions } = useQuery({
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
            />
          )}
          ListboxProps={{ style: { maxHeight: 300 } }}
          sx={{ minWidth: 300 }}
        />

        {isPrototype && (
          <TextField
            select
            size="small"
            label="Version"
            value={versionId}
            onChange={handleVersionChange}
            sx={{ minWidth: 220 }}
            required
            SelectProps={{
              MenuProps: {
                PaperProps: { style: { maxHeight: 300, overflowY: "auto" } },
              },
            }}
          >
            <MenuItem value="" disabled>
              Choose a version
            </MenuItem>
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
                bgcolor: isFilterApplied ? "primary.lighter" : "transparent",
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
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            {!projectId ? "Start by Selecting a Project" : "Select a Version"}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {!projectId
              ? "Pick a project from the dropdown above to load its sessions."
              : "Pick a version from the dropdown above to load sessions."}
          </Typography>
        </Box>
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
          <Box sx={{ flex: 1 }}>
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

function CallStatusCellRenderer({ data }) {
  if (!data) return null;
  const details = data.callDetails || data;
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
  const details = data.call_details || {};
  const name = details.customer_name || details.scenario || "";
  const type =
    details.simulation_call_type || details.call_type || data.call_type || "";
  const startTime = details.start_time || data.timestamp;
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
      {timeStr && (
        <Typography variant="caption" color="text.secondary">
          {timeStr}
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
  const startedAt = run.start_time ?? run.startTime;
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

function SimulationSelector({ onSetSelection }) {
  const [testId, setTestId] = useState("");
  const [executionRunId, setExecutionRunId] = useState("");
  const [gridApi, setGridApi] = useState(null);
  const gridRef = useRef(null);
  const agTheme = useAgThemeWith(SELECTOR_GRID_THEME_PARAMS);
  const queryClient = useQueryClient();

  // 1. Fetch list of tests (infinite)
  const {
    testsList: tests,
    fetchNextPage: fetchNextTestsPage,
    isFetchingNextPage: isFetchingNextTestsPage,
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
  const { data: executionRuns } = useQuery({
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
        }
      },
    }),
    [executionRunId, queryClient],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Call Details",
        field: "callDetails",
        flex: 2,
        minWidth: 220,
        cellRenderer: CallDetailSimpleCellRenderer,
      },
      {
        headerName: "Status",
        field: "status",
        flex: 0.8,
        minWidth: 120,
        cellRenderer: CallStatusCellRenderer,
      },
      {
        headerName: "Timestamp",
        field: "timestamp",
        flex: 1,
        minWidth: 160,
        valueFormatter: (p) => {
          if (!p.value) return "-";
          try {
            return new Date(p.value).toLocaleString("en-US", {
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
        headerName: "Response Time",
        field: "responseTime",
        flex: 0.7,
        minWidth: 120,
        valueFormatter: (p) => (p.value != null ? `${p.value}s` : "-"),
      },
      {
        headerName: "Latency",
        field: "avgAgentLatency",
        flex: 0.7,
        minWidth: 110,
        valueFormatter: (p) => (p.value != null ? `${p.value}ms` : "-"),
      },
    ],
    [],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: false,
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
    onSetSelection([]);
  };

  const handleExecutionRunChange = (e) => {
    setExecutionRunId(e.target.value);
    onSetSelection([]);
  };

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
          value={testId}
          onChange={handleTestChange}
          sx={{ minWidth: 300 }}
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
            Choose a test
          </MenuItem>
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
            value={executionRunId}
            onChange={handleExecutionRunChange}
            sx={{ minWidth: 340 }}
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
              Choose an execution run
            </MenuItem>
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
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            Start by Selecting a Test
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Pick a test from the dropdown above, then select an execution run.
          </Typography>
        </Box>
      )}

      {testId && !executionRunId && (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            Select an Execution Run
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Pick an execution run to view its calls and chats.
          </Typography>
        </Box>
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
          <Box sx={{ flex: 1 }}>
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
