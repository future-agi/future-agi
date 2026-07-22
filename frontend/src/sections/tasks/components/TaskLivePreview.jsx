import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { canonicalEntries } from "src/utils/utils";
import { ROW_TYPE_LABELS } from "src/utils/constants";
import { executeEvalForRow } from "src/sections/evals/utils/evalExecution";
import {
  resolvePath,
  sortSpansForMapping,
} from "src/sections/evals/utils/rowPathWalker";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

import { JsonValueTree } from "src/sections/evals/components/DatasetTestMode";
import EvalResultDisplay from "src/sections/evals/components/EvalResultDisplay";
import SpanRowList from "src/sections/evals/components/SpanRowList";
import {
  InlineAudio,
  RecordingGroup,
} from "src/components/inline-audio/inline-row-audio";
import {
  collectRecordingTracks,
  isAudioKey,
  isAudioUrlString,
  isRecordingObjectKey,
} from "src/components/inline-audio/audio-detection";
import { ID_ONLY_FIELDS } from "src/sections/projects/LLMTracing/idFields";
import {
  ANNOTATION_COLUMN_IDS,
  FIELD_CATEGORY_TO_COL_TYPE,
  RANGE_OPS,
  LIST_OPS,
  NO_VALUE_OPS,
} from "src/sections/common/EvalsTasks/common";

// One form row → one wire entry. No cross-row merging: it would collapse
// "not_contains A AND not_contains B" into "in [A, B]" (inverting intent) and
// is unsupported for numbers (the BE has no number `in`). OR is expressed
// within a single multi-value `in`/`not_in` row.
// eslint-disable-next-line react-refresh/only-export-components
export function buildApiFilterArray(oldFormatFilters, startDate, endDate) {
  const userFilters = (oldFormatFilters || [])
    .map((f) => {
      const isAttribute = f.property === "attributes";
      const columnId = isAttribute ? f.propertyId : f.property;
      if (!columnId) return null;
      const op = f?.filterConfig?.filterOp || "equals";
      const filterType = f?.filterConfig?.filterType || "text";
      const v = f?.filterConfig?.filterValue;
      const isIdColumn = ID_ONLY_FIELDS.has(columnId);
      // apiColType is source of truth; fieldCategory/isAttribute are UI hints.
      const colType = ANNOTATION_COLUMN_IDS.has(columnId)
        ? "ANNOTATION"
        : f.apiColType ||
          FIELD_CATEGORY_TO_COL_TYPE[f.fieldCategory] ||
          (isAttribute ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC");
      let filterValue;
      if (NO_VALUE_OPS.has(op)) {
        filterValue = "";
      } else if (RANGE_OPS.has(op)) {
        if (Array.isArray(v) && v.length > 0) filterValue = v;
      } else if (LIST_OPS.has(op)) {
        const arr = Array.isArray(v) ? v : v != null && v !== "" ? [v] : [];
        if (arr.length > 0) filterValue = arr;
      } else if (v !== undefined && v !== null && v !== "") {
        filterValue = v;
      }
      return {
        column_id: columnId,
        filter_config: {
          filter_type: filterType,
          filter_op: op,
          ...(filterValue !== undefined && { filter_value: filterValue }),
          ...(!isIdColumn && { col_type: colType }),
        },
      };
    })
    // Drop value-less in/not_in (legacy/hand-edited)
    .filter(
      (entry) =>
        entry &&
        (!LIST_OPS.has(entry.filter_config.filter_op) ||
          entry.filter_config.filter_value !== undefined),
    );

  if (startDate && endDate) {
    userFilters.push({
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: [
          new Date(startDate).toISOString(),
          new Date(endDate).toISOString(),
        ],
      },
    });
  }

  return userFilters;
}

// Deep search: check if a value (including nested JSON) matches query
function deepMatch(val, q) {
  if (val === null || val === undefined) return false;
  if (typeof val === "string") return val.toLowerCase().includes(q);
  if (typeof val === "number" || typeof val === "boolean")
    return String(val).toLowerCase().includes(q);
  if (Array.isArray(val)) return val.some((v) => deepMatch(v, q));
  if (typeof val === "object") {
    return Object.entries(val).some(
      ([k, v]) => k.toLowerCase().includes(q) || deepMatch(v, q),
    );
  }
  return false;
}

// Sort entries so span_attributes, input, output, metadata come first
const PRIORITY_KEYS = ["span_attributes", "input", "output", "metadata"];
function sortEntries(entries) {
  return [...entries].sort(([a], [b]) => {
    const ai = PRIORITY_KEYS.indexOf(a);
    const bi = PRIORITY_KEYS.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return 0;
  });
}

// Find span by id recursively in the observation spans tree.
function findSpanInTree(spans, spanId) {
  if (!spans) return null;
  for (const item of spans) {
    const span = item.observation_span;
    if (span?.id === spanId) return span;
    if (item.children?.length) {
      const found = findSpanInTree(item.children, spanId);
      if (found) return found;
    }
  }
  return null;
}

// Flatten span tree into an ordered list with smart indexing.
function flattenSpanTree(
  spans,
  depth = 0,
  parentPath = "",
  nameCountMap = null,
) {
  if (!spans) return [];
  const isRoot = nameCountMap === null;
  if (isRoot) nameCountMap = {};
  const result = [];

  for (const item of spans) {
    const obsSpan = item.observation_span;
    if (obsSpan) {
      const s = obsSpan;
      const name = s.name || "span";
      nameCountMap[name] = (nameCountMap[name] || 0) + 1;
      const nameIndex = nameCountMap[name];
      const path = parentPath ? `${parentPath} › ${name}` : name;

      result.push({
        ...s,
        _depth: depth,
        _path: path,
        _nameIndex: nameIndex,
        _nameTotal: 0,
      });

      if (item.children?.length) {
        result.push(
          ...flattenSpanTree(item.children, depth + 1, path, nameCountMap),
        );
      }
    }
  }

  if (isRoot) {
    for (const span of result) {
      span._nameTotal = nameCountMap[span.name || "span"] || 1;
    }
  }

  return result;
}

// ───────────────────────────────────────────────────────────────
// Main
// ───────────────────────────────────────────────────────────────
const TaskLivePreview = forwardRef(function TaskLivePreview(
  { control, projectId, onTestStateChange },
  ref,
) {
  const [currentRowIndex, setCurrentRowIndex] = useState(0);
  const [tableSearch, setTableSearch] = useState("");
  const [expandedCols, setExpandedCols] = useState({});
  // Per-eval test results keyed by eval index:
  //   { [idx]: { status: "running" | "success" | "error", result?, error? } }
  const [testResults, setTestResults] = useState({});
  const [isTesting, setIsTesting] = useState(false);

  const formFilters = useWatch({ control, name: "filters" });
  const startDate = useWatch({ control, name: "startDate" });
  const endDate = useWatch({ control, name: "endDate" });
  const evalsDetails = useWatch({ control, name: "evalsDetails" });
  const rowType = useWatch({ control, name: "rowType" }) || "spans";

  const apiFilters = useMemo(
    () => buildApiFilterArray(formFilters, startDate, endDate),
    [formFilters, startDate, endDate],
  );

  // Reset row index when filters / rowType change
  useEffect(() => {
    setCurrentRowIndex(0);
  }, [apiFilters, rowType, projectId]);

  // ── Fetch list of matching rows ──
  const {
    data: listData,
    isLoading: listLoading,
    isFetching: listFetching,
    isError: listError,
  } = useQuery({
    queryKey: ["task-preview-list", rowType, projectId, apiFilters],
    queryFn: async () => {
      if (!projectId) return { rows: [], total: 0, columns: [] };

      if (rowType === "voiceCalls") {
        const resp = await axios.get(endpoints.project.getCallLogs, {
          params: {
            project_id: projectId,
            page: 1,
            page_size: 50,
            filters: JSON.stringify(apiFilters),
          },
        });
        const result = resp.data?.result || resp.data || {};
        const rowsOut = result.results || result.data || result.calls || [];
        return {
          rows: rowsOut,
          total: result.total_count || result.total || rowsOut.length,
          columns: [],
        };
      }

      let url;
      switch (rowType) {
        case "traces":
          url = endpoints.project.getTracesForObserveProject();
          break;
        case "spans":
          url = endpoints.project.getSpansForObserveProject();
          break;
        case "sessions":
          url = endpoints.project.projectSessionList();
          break;
        default:
          url = endpoints.project.getSpansForObserveProject();
      }

      const resp = await axios.get(url, {
        params: {
          project_id: projectId,
          page_number: 0,
          page_size: 50,
          filters: JSON.stringify(apiFilters),
        },
      });
      const result = resp.data?.result || {};
      return {
        rows: result.table || result.results || result.data || [],
        total:
          result.metadata?.total_rows ||
          result.total_count ||
          result.total ||
          (result.table || []).length,
        columns: result.config || [],
      };
    },
    enabled: !!projectId,
    refetchOnWindowFocus: false,
    staleTime: 10000,
  });

  const rows = listData?.rows || [];
  const total = listData?.total || 0;
  const columns = listData?.columns || [];
  const currentRow = rows[currentRowIndex] || null;

  // ── Fetch full detail for the currently selected row ──
  const { data: spanDetail, isLoading: detailLoading } = useQuery({
    queryKey: [
      "task-preview-detail",
      rowType,
      currentRow?.trace_id,
      currentRow?.span_id,
      currentRow?.session_id,
    ],
    queryFn: async () => {
      if (!currentRow) return null;
      const spanId = currentRow.span_id;
      const traceId = currentRow.trace_id;

      let detailData = null;

      // Voice calls → dedicated voice_call_detail endpoint with transcript,
      // recording URLs, scenario info, customer info, latency metrics, etc.
      if (rowType === "voiceCalls" && traceId) {
        try {
          const { data } = await axios.get(
            endpoints.project.getVoiceCallDetail,
            { params: { trace_id: traceId } },
          );
          const voiceResult = data?.result || data?.data || data || {};
          detailData = { ...currentRow, ...voiceResult };
        } catch {
          detailData = { ...currentRow };
        }
      } else if ((rowType === "spans" || rowType === "traces") && traceId) {
        const { data } = await axios.get(endpoints.project.getTrace(traceId));
        const traceResult = data?.result;

        const spans = traceResult?.observation_spans;
        if (rowType === "spans" && spanId && spans) {
          detailData = findSpanInTree(spans, spanId);
          if (!detailData) {
            detailData = spans?.[0]?.observation_span || traceResult?.trace;
          }
        } else {
          const traceInfo = traceResult?.trace || {};
          const allSpans = sortSpansForMapping(flattenSpanTree(spans));
          detailData = { ...traceInfo, spans: allSpans };
        }
      } else if (rowType === "sessions" && currentRow?.session_id) {
        // Sessions need a layered fetch: list_sessions returns flat
        // session-summary rows (id, total_cost, traces_count, etc.) but
        // no nested traces/spans. resolvePath (the "(not in row)" check)
        // needs the actual session shape so mapping paths like
        // `traces.<i>.input` and
        // `traces.0.spans.<j>.<key>` resolve. Two-step fetch:
        //   1) GET /tracer/trace-session/<id>/ → paginated trace list
        //      (no spans nested per the BE contract)
        //   2) GET /tracer/trace/<first_trace_id>/ → spans tree for the
        //      first trace. Span-level mapping paths only resolve for
        //      the first trace in the preview; deeper drill-in would
        //      need a click-to-fetch UI that we don't have here yet.
        const sid = currentRow.session_id;
        let sessionMeta = {};
        let traces = [];
        try {
          const sResp = await axios.get(
            `${endpoints.project.traceSession}${sid}/`,
            { params: { page_number: 0, page_size: 30 } },
          );
          const sResult = sResp.data?.result || {};
          sessionMeta = sResult.session_metadata || {};
          traces = sResult.response || [];
        } catch {
          // Session fetch failed — fall back to the flat row so the
          // user at least sees session-summary fields.
          detailData = { ...currentRow };
        }

        if (!detailData) {
          let firstTraceSpans = [];
          const firstTraceId = traces[0]?.trace_id;
          if (firstTraceId) {
            try {
              const tResp = await axios.get(
                endpoints.project.getTrace(firstTraceId),
              );
              const tResult = tResp.data?.result || {};
              firstTraceSpans = sortSpansForMapping(
                flattenSpanTree(tResult.observation_spans || []),
              );
            } catch {
              // Trace fetch failed — leave empty; the rest of the
              // preview still works with trace-level fields.
            }
          }
          detailData = {
            ...sessionMeta,
            traces: traces.map((t, i) => ({
              ...t,
              spans: i === 0 ? firstTraceSpans : [],
              ...(i === 0 ? {} : { _spansLoaded: false }),
            })),
          };
        }
      } else {
        detailData = { ...currentRow };
      }

      return detailData;
    },
    enabled: !!currentRow,
    refetchOnWindowFocus: false,
    staleTime: 10000,
  });

  // Reset test results whenever the row or eval set changes
  useEffect(() => {
    setTestResults({});
  }, [currentRow, evalsDetails?.length]);

  // ── Test all configured evals on the current row ──
  const handleRunTest = useCallback(async () => {
    if (!currentRow || !evalsDetails?.length || !spanDetail) return;

    setIsTesting(true);
    setTestResults(
      evalsDetails.reduce((acc, _ev, idx) => {
        acc[idx] = { status: "running" };
        return acc;
      }, {}),
    );

    await Promise.all(
      evalsDetails.map(async (evalItem, idx) => {
        const templateId = evalItem?.template_id;
        if (!templateId) {
          setTestResults((prev) => ({
            ...prev,
            [idx]: {
              status: "error",
              error: "Missing template id — re-add this eval",
            },
          }));
          return;
        }
        // Forward the saved data_injection flags so the BE enables the
        // matching auto-context (matches EvalPickerConfigFull's tracing tab).
        const diFlags =
          evalItem?.data_injection ||
          evalItem?.config?.run_config?.data_injection ||
          evalItem?.config?.data_injection ||
          {};
        const configExtras =
          Object.keys(diFlags).length > 0
            ? { run_config: { data_injection: diFlags } }
            : {};
        const result = await executeEvalForRow({
          evalItem,
          rowType,
          currentRow,
          spanDetail,
          mapping: evalItem?.mapping || {},
          singleEvalConfigExtras: configExtras,
          compositeConfigExtras: configExtras,
        });
        if (result.ok) {
          setTestResults((prev) => ({
            ...prev,
            [idx]: {
              status: "success",
              // EvalResultDisplay reads `compositeResult` for composite or
              // the legacy `output`/`reason`/`score` keys for single.
              result: result.isComposite
                ? {
                    output: result.output,
                    reason: result.reason,
                    compositeResult: result.compositeResult,
                  }
                : result.raw,
            },
          }));
        } else {
          setTestResults((prev) => ({
            ...prev,
            [idx]: {
              status: "error",
              error: result.errorMessage || "Failed to run eval",
            },
          }));
        }
      }),
    );
    setIsTesting(false);
  }, [currentRow, evalsDetails, spanDetail, rowType]);

  // Expose runTest to parent via ref so the Test button in the page
  // footer can trigger it
  useImperativeHandle(
    ref,
    () => ({
      runTest: handleRunTest,
    }),
    [handleRunTest],
  );

  // Notify parent of test-readiness + loading state so it can enable /
  // disable / spin the footer Test button
  useEffect(() => {
    if (!onTestStateChange) return;
    onTestStateChange({
      canTest: !!currentRow && !!spanDetail && (evalsDetails?.length || 0) > 0,
      isTesting,
    });
  }, [
    currentRow,
    spanDetail,
    evalsDetails?.length,
    isTesting,
    onTestStateChange,
  ]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* ── Header ── */}
      <Box sx={{ px: 2, pt: 2, pb: 1, flexShrink: 0 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 0.5,
            gap: 1,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
            <Typography
              variant="subtitle2"
              fontWeight={600}
              sx={{ fontSize: "13px" }}
            >
              Live Preview
            </Typography>
            {projectId && (
              <Chip
                label={ROW_TYPE_LABELS[rowType] || rowType}
                size="small"
                sx={{
                  height: 18,
                  fontSize: "10px",
                  bgcolor: "background.neutral",
                  color: "text.secondary",
                  "& .MuiChip-label": { px: 0.75 },
                  "&:hover": { bgcolor: "background.neutral" },
                }}
              />
            )}
          </Box>
          {listFetching && <CircularProgress size={12} />}
        </Box>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "11px", display: "block" }}
        >
          {projectId
            ? "Browse a sample row matching your current filters"
            : "Select a project to preview matching rows"}
        </Typography>
      </Box>

      <Divider />

      {/* ── Content ── */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 1.5 }}>
        {!projectId ? (
          <EmptyState
            icon="solar:filter-outline"
            text="Select a project to preview matching rows"
          />
        ) : listLoading ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: 160,
            }}
          >
            <CircularProgress size={20} />
          </Box>
        ) : listError ? (
          <Typography
            variant="body2"
            color="error"
            sx={{ fontSize: "12px", textAlign: "center", mt: 2 }}
          >
            Failed to load preview
          </Typography>
        ) : rows.length === 0 ? (
          <EmptyState
            icon="solar:magnifer-outline"
            text="No matching rows"
            subtext="Adjust filters to see matching data"
          />
        ) : (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            {/* Row navigator */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 1,
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "11px" }}
              >
                Row {Math.min(currentRowIndex + 1, rows.length)} of{" "}
                {rows.length}
                {total > rows.length && (
                  <Typography
                    component="span"
                    sx={{ fontSize: "11px", color: "text.disabled", ml: 0.5 }}
                  >
                    ({total} matching total)
                  </Typography>
                )}
              </Typography>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <IconButton
                  size="small"
                  disabled={currentRowIndex === 0}
                  onClick={() => setCurrentRowIndex((i) => Math.max(0, i - 1))}
                  sx={{ width: 24, height: 24 }}
                >
                  <Iconify icon="mdi:chevron-left" width={16} />
                </IconButton>
                <IconButton
                  size="small"
                  disabled={currentRowIndex >= rows.length - 1}
                  onClick={() =>
                    setCurrentRowIndex((i) => Math.min(rows.length - 1, i + 1))
                  }
                  sx={{ width: 24, height: 24 }}
                >
                  <Iconify icon="mdi:chevron-right" width={16} />
                </IconButton>
              </Box>
            </Box>

            {/* Detail table */}
            {detailLoading ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
                <CircularProgress size={18} />
              </Box>
            ) : spanDetail ? (
              <RowDetailTable
                spanDetail={spanDetail}
                tableSearch={tableSearch}
                setTableSearch={setTableSearch}
                expandedCols={expandedCols}
                setExpandedCols={setExpandedCols}
                columns={columns}
              />
            ) : null}

            {/* Variable mapping — shows per-eval mapping + inline test
                results. Test button itself lives in the page footer. */}
            {spanDetail && (
              <VariableMappingView
                evalsDetails={evalsDetails || []}
                spanDetail={spanDetail}
                testResults={testResults}
              />
            )}
          </Box>
        )}
      </Box>
    </Box>
  );
});

TaskLivePreview.propTypes = {
  control: PropTypes.object.isRequired,
  projectId: PropTypes.string,
  onTestStateChange: PropTypes.func,
};

// ───────────────────────────────────────────────────────────────
// Row detail table (single row — all columns/values)
// ───────────────────────────────────────────────────────────────
const RowDetailTable = ({
  spanDetail,
  tableSearch,
  setTableSearch,
  expandedCols,
  setExpandedCols,
  columns: _columns,
}) => {
  // Flatten span_attributes children into the top-level entries so users
  // see e.g. "llm.system" as its own row instead of a collapsed object.
  // Top-level keys win deduplication (same soft-flatten rowPathWalker uses).
  // The `spans` key is filtered out here because it gets a dedicated
  // collapsible-row renderer below — same pattern as TracingTestMode so
  // the two preview surfaces look identical for trace + session row types.
  // canonicalEntries (not Object.entries) drops the camelCase aliases the
  // axios interceptor adds for any snake_case key — without it, every
  // `gen_ai.span.kind` row would also have a duplicate `genAi.span.kind`
  // sibling rendered next to it.
  const entries = useMemo(() => {
    const raw = canonicalEntries(spanDetail).filter(([key]) => key !== "spans");
    const spanAttrs = spanDetail?.span_attributes;
    if (
      !spanAttrs ||
      typeof spanAttrs !== "object" ||
      Array.isArray(spanAttrs)
    ) {
      return sortEntries(raw);
    }
    const topKeys = new Set(raw.map(([k]) => k));
    const flattened = raw.filter(([k]) => k !== "span_attributes");
    for (const [k, v] of canonicalEntries(spanAttrs)) {
      if (!topKeys.has(k)) {
        flattened.push([k, v]);
      }
    }
    return sortEntries(flattened);
  }, [spanDetail]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {/* Search */}
      <Box
        sx={{
          px: 1,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <TextField
          size="small"
          fullWidth
          placeholder="Search columns or values..."
          value={tableSearch}
          onChange={(e) => setTableSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
            sx: { fontSize: "12px", height: 28 },
          }}
        />
      </Box>

      {/* Header */}
      <Box
        sx={{
          display: "flex",
          px: 1.5,
          py: 0.5,
          backgroundColor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(255,255,255,0.03)"
              : "background.default",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ width: 130, flexShrink: 0 }}
        >
          Columns
        </Typography>
        <Typography variant="caption" fontWeight={600} sx={{ flex: 1 }}>
          Value
        </Typography>
      </Box>

      {/* Rows */}
      <Box sx={{ maxHeight: 360, overflowY: "auto" }}>
        {entries
          .filter(([key, val]) => {
            if (!tableSearch.trim()) return true;
            const q = tableSearch.toLowerCase();
            return key.toLowerCase().includes(q) || deepMatch(val, q);
          })
          .map(([key, val]) => {
            const isObj =
              val !== null &&
              val !== undefined &&
              typeof val === "object" &&
              !Array.isArray(val);
            const isArr = Array.isArray(val);
            const isEmpty =
              val === null ||
              val === undefined ||
              val === "" ||
              (isObj && Object.keys(val).length === 0) ||
              (isArr && val.length === 0);

            // Audio detection
            const isRecordingObject = isObj && isRecordingObjectKey(key);
            const recordingTracks = isRecordingObject
              ? collectRecordingTracks(val)
              : [];
            const isPlayableString =
              typeof val === "string" &&
              (isAudioKey(key) || isAudioUrlString(val));

            return (
              <Box
                key={key}
                sx={{
                  display: "flex",
                  alignItems: "flex-start",
                  px: 1.5,
                  py: 0.6,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  "&:last-child": { borderBottom: "none" },
                  "&:hover": { backgroundColor: "action.hover" },
                }}
              >
                <CustomTooltip
                  title={key}
                  show
                  placement="top-start"
                  arrow
                  size="small"
                >
                  <Typography
                    variant="caption"
                    fontWeight={500}
                    noWrap
                    sx={{ width: 130, flexShrink: 0, pt: 0.25 }}
                  >
                    {key}
                  </Typography>
                </CustomTooltip>
                <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                  {isEmpty ? (
                    <Typography variant="caption" color="text.disabled">
                      —
                    </Typography>
                  ) : isPlayableString ? (
                    <InlineAudio src={val} />
                  ) : isRecordingObject && recordingTracks.length > 0 ? (
                    <RecordingGroup tracks={recordingTracks} />
                  ) : isObj || isArr ? (
                    <JsonValueTree
                      value={val}
                      expanded={expandedCols[key]}
                      onToggle={() =>
                        setExpandedCols((prev) => ({
                          ...prev,
                          [key]: !prev[key],
                        }))
                      }
                    />
                  ) : (
                    <Typography
                      variant="caption"
                      color="primary.main"
                      sx={{
                        fontSize: "12px",
                        wordBreak: "break-all",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        display: "-webkit-box",
                        WebkitLineClamp: expandedCols[key] ? 999 : 2,
                        WebkitBoxOrient: "vertical",
                        cursor: "pointer",
                      }}
                      onClick={() =>
                        setExpandedCols((prev) => ({
                          ...prev,
                          [key]: !prev[key],
                        }))
                      }
                    >
                      {/* Defensive: this branch should only be reached
                          for primitives because the upstream isObj/isArr
                          check routes objects to JsonValueTree. But if
                          something slips through (e.g. a class instance
                          with weird typeof), JSON.stringify it instead
                          of falling back to "[object Object]". */}
                      {typeof val === "boolean"
                        ? String(val)
                        : typeof val === "string"
                          ? `"${val}"`
                          : val !== null && typeof val === "object"
                            ? JSON.stringify(val)
                            : String(val)}
                    </Typography>
                  )}
                </Box>
              </Box>
            );
          })}

        {/* Spans section — each span as a collapsible row. Shared
            renderer with TracingTestMode so the picker drawer and the
            preview pane look identical for trace + session row types. */}
        <SpanRowList
          spans={spanDetail.spans}
          expandedCols={expandedCols}
          setExpandedCols={setExpandedCols}
          tableSearch={tableSearch}
        />
      </Box>
    </Box>
  );
};

RowDetailTable.propTypes = {
  spanDetail: PropTypes.object.isRequired,
  tableSearch: PropTypes.string.isRequired,
  setTableSearch: PropTypes.func.isRequired,
  expandedCols: PropTypes.object.isRequired,
  setExpandedCols: PropTypes.func.isRequired,
  columns: PropTypes.array,
};

// ───────────────────────────────────────────────────────────────
// Variable mapping view — per configured eval (read-only) + test runner
// ───────────────────────────────────────────────────────────────
const VariableMappingView = ({
  evalsDetails,
  spanDetail,
  testResults = {},
}) => {
  const hasEvals = evalsDetails.length > 0;

  if (!hasEvals) return null;

  return (
    <Box>
      <Box sx={{ mb: 0.75 }}>
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ display: "block", fontSize: "11px" }}
        >
          Variable Mapping
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", fontSize: "10px" }}
        >
          Configured mapping for each eval against the current row&apos;s fields
        </Typography>
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {evalsDetails.map((evalItem, idx) => {
          const name =
            evalItem?.name ||
            evalItem?.evalTemplate?.name ||
            evalItem?.evalTemplateName ||
            `Evaluation ${idx + 1}`;
          const mapping = evalItem?.mapping || {};
          const variables = Object.keys(mapping);

          // Eval-type-aware metadata — mirrors ConfiguredEvalCard logic
          const evalType = (
            evalItem?.evalType ||
            evalItem?.evalTemplate?.evalType ||
            "llm"
          ).toLowerCase();
          const isCode = evalType === "code";
          const model = evalItem?.model;
          const codeLang =
            evalItem?.config?.language ||
            evalItem?.evalTemplate?.config?.language ||
            (isCode ? "Python" : null);

          return (
            <Box
              key={evalItem?.id || idx}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                p: 1,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.75,
                  mb: variables.length > 0 ? 0.75 : 0,
                }}
              >
                <Iconify
                  icon="solar:test-tube-linear"
                  width={12}
                  sx={{ color: "primary.main" }}
                />
                <Typography
                  variant="caption"
                  fontWeight={600}
                  sx={{ fontSize: "12px" }}
                >
                  {name}
                </Typography>
                {isCode && codeLang && (
                  <Chip
                    label={
                      codeLang.charAt(0).toUpperCase() +
                      codeLang.slice(1).toLowerCase()
                    }
                    size="small"
                    sx={{
                      height: 16,
                      fontSize: "9px",
                      bgcolor: "background.neutral",
                      color: "text.secondary",
                      "& .MuiChip-label": { px: 0.5 },
                      "&:hover": { bgcolor: "background.neutral" },
                    }}
                  />
                )}
                {!isCode && model && (
                  <Chip
                    label={model}
                    size="small"
                    sx={{
                      height: 16,
                      fontSize: "9px",
                      bgcolor: "background.neutral",
                      color: "text.secondary",
                      "& .MuiChip-label": { px: 0.5 },
                      "&:hover": { bgcolor: "background.neutral" },
                    }}
                  />
                )}
              </Box>
              {variables.length === 0 ? (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ fontSize: "10px" }}
                >
                  No variables mapped
                </Typography>
              ) : (
                <Box
                  sx={{ display: "flex", flexDirection: "column", gap: 0.4 }}
                >
                  {variables.map((variable) => {
                    const field = mapping[variable];
                    // Tri-state: `missing` warns, `unknown` (session traces
                    // whose spans weren't fetched) stays silent — the BE is
                    // the authoritative resolver at test time.
                    const hit = spanDetail
                      ? resolvePath(spanDetail, field)
                      : { status: "unknown" };
                    const showWarn = hit.status === "missing" && !!field;
                    return (
                      <Box
                        key={variable}
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          gap: 0.5,
                          pl: 2,
                        }}
                      >
                        <Iconify
                          icon="mdi:code-braces"
                          width={11}
                          sx={{ color: "text.disabled" }}
                        />
                        <Typography
                          variant="caption"
                          fontWeight={600}
                          sx={{ fontSize: "11px" }}
                        >
                          {variable}
                        </Typography>
                        <Iconify
                          icon="mdi:arrow-right"
                          width={11}
                          sx={{ color: "text.disabled" }}
                        />
                        <CustomTooltip
                          title={field || ""}
                          show={!!field}
                          type="default"
                          placement="top"
                          arrow
                          size="small"
                        >
                          <Typography
                            variant="caption"
                            sx={{
                              fontSize: "11px",
                              fontFamily: "monospace",
                              color: showWarn ? "warning.main" : "primary.main",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {field || "—"}
                          </Typography>
                        </CustomTooltip>
                        {showWarn && (
                          <Typography
                            variant="caption"
                            color="warning.main"
                            sx={{ fontSize: "10px", ml: 0.25 }}
                          >
                            (not in row)
                          </Typography>
                        )}
                      </Box>
                    );
                  })}
                </Box>
              )}

              {/* Test result for this eval */}
              {testResults?.[idx] && (
                <Box
                  sx={{
                    mt: 1,
                    pt: 1,
                    borderTop: "1px dashed",
                    borderColor: "divider",
                  }}
                >
                  {testResults[idx].status === "running" && (
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                      }}
                    >
                      <CircularProgress size={12} thickness={5} />
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ fontSize: "11px" }}
                      >
                        Running eval…
                      </Typography>
                    </Box>
                  )}
                  {testResults[idx].status === "success" && (
                    <EvalResultDisplay result={testResults[idx].result} />
                  )}
                  {testResults[idx].status === "error" && (
                    <Box
                      sx={(theme) => ({
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 0.5,
                        p: 0.75,
                        borderRadius: "4px",
                        // error.lighter is a fixed light pink (#F8D5D5)
                        // that clashes with dark mode — derive from main.
                        bgcolor: alpha(
                          theme.palette.error.main,
                          theme.palette.mode === "dark" ? 0.16 : 0.08,
                        ),
                        border: "1px solid",
                        borderColor: alpha(theme.palette.error.main, 0.4),
                      })}
                    >
                      <Iconify
                        icon="solar:danger-triangle-linear"
                        width={12}
                        sx={{ color: "error.main", mt: 0.15 }}
                      />
                      <Typography
                        variant="caption"
                        color="error.main"
                        sx={{ fontSize: "11px" }}
                      >
                        {testResults[idx].error}
                      </Typography>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          );
        })}
      </Box>
    </Box>
  );
};

VariableMappingView.propTypes = {
  evalsDetails: PropTypes.array.isRequired,
  spanDetail: PropTypes.object,
  testResults: PropTypes.object,
};

// ───────────────────────────────────────────────────────────────
const EmptyState = ({ icon, text, subtext }) => (
  <Box
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: 1,
      py: 6,
    }}
  >
    <Iconify icon={icon} width={32} sx={{ color: "text.disabled" }} />
    <Typography variant="body2" color="text.disabled" sx={{ fontSize: "12px" }}>
      {text}
    </Typography>
    {subtext && (
      <Typography
        variant="caption"
        color="text.disabled"
        sx={{ fontSize: "11px" }}
      >
        {subtext}
      </Typography>
    )}
  </Box>
);

EmptyState.propTypes = {
  icon: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  subtext: PropTypes.string,
};

export default TaskLivePreview;
