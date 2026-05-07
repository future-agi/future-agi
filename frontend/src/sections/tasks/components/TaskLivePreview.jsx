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
import { canonicalEntries, stripAttributePathPrefix } from "src/utils/utils";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

import { JsonValueTree } from "src/sections/evals/components/DatasetTestMode";
import EvalResultDisplay from "src/sections/evals/components/EvalResultDisplay";
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

// ───────────────────────────────────────────────────────────────
// Helpers (ported from TracingTestMode)
// ───────────────────────────────────────────────────────────────
const COL_TYPE_MAP = {
  attribute: "SPAN_ATTRIBUTE",
  system: "SYSTEM_METRIC",
  eval: "EVALUATION_METRIC",
  annotation: "ANNOTATION",
};

// eslint-disable-next-line react-refresh/only-export-components
export function buildApiFilterArray(oldFormatFilters, startDate, endDate) {
  const userFilters = (oldFormatFilters || [])
    .filter((f) => f?.propertyId || f?.property)
    .map((f) => {
      const isAttribute = f.property === "attributes";
      const columnId = isAttribute ? f.propertyId : f.property;
      const colType =
        COL_TYPE_MAP[f.fieldCategory] ||
        (isAttribute ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC");
      return {
        column_id: columnId,
        filter_config: {
          filter_type: f?.filterConfig?.filterType || "text",
          filter_op: f?.filterConfig?.filterOp || "equals",
          filter_value: f?.filterConfig?.filterValue,
          col_type: colType,
        },
      };
    });

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

const ROW_TYPE_LABEL = {
  spans: "Spans",
  traces: "Traces",
  sessions: "Sessions",
  voiceCalls: "Voice Calls",
};

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

      // Voice calls use a dedicated list_voice_calls endpoint with a
      // different request/response shape (no filter array).
      if (rowType === "voiceCalls") {
        const resp = await axios.get(endpoints.project.getCallLogs, {
          params: {
            project_id: projectId,
            page: 1,
            page_size: 50,
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
          const allSpans = flattenSpanTree(spans);
          detailData = { ...traceInfo, spans: allSpans };
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

  // ── Available field paths for variable mapping (dot notation for nested) ──
  // Soft-flatten: attributes inside `span_attributes.*` are surfaced as
  // bare names (e.g. `input` instead of `span_attributes.input`) so users
  // can map variables to short field names. Top-level
  // fields with the same name win the deduplication. Stored mapping values
  // keep the stripped name; the resolver below transparently falls back
  // to `span_attributes.<name>` if the top-level lookup misses — existing
  // tasks stored with the full `span_attributes.` prefix continue to work.
  const fieldNames = useMemo(() => {
    if (!spanDetail) return [];
    const keys = [];
    // Limits match the resolver walker below so every path this component
    // claims is in the row is actually resolvable at test time — otherwise
    // the "(not in row)" chip lies for deep paths that do resolve.
    const ARRAY_PEEK = 500;
    const DICT_LIMIT = 5000;
    const walk = (node, prefix) => {
      if (Array.isArray(node)) {
        node.slice(0, ARRAY_PEEK).forEach((item, idx) => {
          const path = prefix ? `${prefix}.${idx}` : String(idx);
          keys.push(path);
          if (item && typeof item === "object") {
            walk(item, path);
          }
        });
        return;
      }
      for (const [k, v] of canonicalEntries(node)) {
        if (k.startsWith("_")) continue;
        const path = prefix ? `${prefix}.${k}` : k;
        keys.push(path);
        if (v && typeof v === "object") {
          if (Array.isArray(v) || Object.keys(v).length < DICT_LIMIT) {
            walk(v, path);
          }
        }
      }
    };
    walk(spanDetail, "");
    // Strip wrapper/span_attributes prefix and dedupe against top-level keys.
    const seen = new Set();
    const flattened = [];
    keys.forEach((k) => {
      const short = stripAttributePathPrefix(k);
      if (seen.has(short)) return;
      seen.add(short);
      flattened.push(short);
    });
    return flattened;
  }, [spanDetail]);

  // Reset test results whenever the row or eval set changes
  useEffect(() => {
    setTestResults({});
  }, [currentRow, evalsDetails?.length]);

  // ── Test all configured evals on the current row ──
  const handleRunTest = useCallback(async () => {
    if (!currentRow || !evalsDetails?.length || !spanDetail) return;

    const _spanId = currentRow?.span_id;
    const _traceId = currentRow?.trace_id;
    const _sessionId = currentRow?.session_id;

    const autoCtx = {};
    if (rowType === "spans" && _spanId) autoCtx.span_id = _spanId;
    if ((rowType === "spans" || rowType === "traces") && _traceId)
      autoCtx.trace_id = _traceId;
    if (rowType === "sessions" && _sessionId) autoCtx.session_id = _sessionId;
    if (rowType === "voiceCalls" && _traceId) autoCtx.trace_id = _traceId;

    // Build a flat fieldName→value lookup by walking spanDetail,
    // soft-flattening span_attributes keys (same logic as the
    // fieldNames dropdown in TracingTestMode). This ensures mapped
    // fields like "input.value" (stripped from "span_attributes.input.value")
    // resolve correctly even when a top-level "input" shadows the path.
    // Limits match the dropdown walker in TracingTestMode so every path
    // offered to the user during mapping also resolves at test time.
    const ARRAY_PEEK = 500;
    const DICT_LIMIT = 5000;
    const valueMap = {};
    const walkValues = (node, prefix) => {
      if (Array.isArray(node)) {
        node.slice(0, ARRAY_PEEK).forEach((item, idx) => {
          const path = prefix ? `${prefix}.${idx}` : String(idx);
          valueMap[path] = item;
          if (item && typeof item === "object") {
            walkValues(item, path);
          }
        });
        return;
      }
      // canonicalEntries drops the camelCase aliases the axios interceptor
      // layers on — otherwise `span_attributes.*` and `spanAttributes.*`
      // both end up in valueMap and only the snake side gets stripped.
      for (const [k, v] of canonicalEntries(node)) {
        if (k.startsWith("_")) continue;
        const path = prefix ? `${prefix}.${k}` : k;
        valueMap[path] = v;
        if (v && typeof v === "object") {
          if (Array.isArray(v) || Object.keys(v).length < DICT_LIMIT) {
            walkValues(v, path);
          }
        }
      }
    };
    walkValues(spanDetail, "");

    // Soft-flatten: strip `span_attributes.` prefix, top-level wins.
    const flatValueMap = {};
    for (const [path, val] of Object.entries(valueMap)) {
      const short = path.startsWith("span_attributes.")
        ? path.slice("span_attributes.".length)
        : path;
      if (!(short in flatValueMap) || !path.startsWith("span_attributes.")) {
        flatValueMap[short] = val;
      }
    }

    const resolveMapping = (mapping) => {
      const resolved = {};
      for (const [variable, field] of Object.entries(mapping || {})) {
        if (!field) continue;
        const val = flatValueMap[field];
        if (val !== undefined && val !== null) {
          resolved[variable] =
            typeof val === "object" ? JSON.stringify(val) : String(val);
        }
      }
      return resolved;
    };

    setIsTesting(true);
    // Initialize all to running
    setTestResults(
      evalsDetails.reduce((acc, _ev, idx) => {
        acc[idx] = { status: "running" };
        return acc;
      }, {}),
    );

    // Run evals in parallel — each one independently
    await Promise.all(
      evalsDetails.map(async (evalItem, idx) => {
        try {
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
          // Build data_injection flags from the eval's saved config
          // so the BE enables the correct context toggles (same as
          // EvalPickerConfigFull does for tracing tab).
          const diFlags =
            evalItem?.data_injection ||
            evalItem?.config?.run_config?.data_injection ||
            evalItem?.config?.data_injection ||
            {};
          const { data } = await axios.post(
            endpoints.develop.eval.evalPlayground,
            {
              template_id: templateId,
              model: evalItem?.model || "turing_large",
              config: {
                mapping: resolveMapping(evalItem?.mapping),
                ...(Object.keys(diFlags).length > 0
                  ? { data_injection: diFlags }
                  : {}),
              },
              ...autoCtx,
            },
          );
          if (data?.status) {
            setTestResults((prev) => ({
              ...prev,
              [idx]: { status: "success", result: data.result },
            }));
          } else {
            setTestResults((prev) => ({
              ...prev,
              [idx]: {
                status: "error",
                error: data?.result || "Evaluation failed",
              },
            }));
          }
        } catch (err) {
          setTestResults((prev) => ({
            ...prev,
            [idx]: {
              status: "error",
              error:
                err?.response?.data?.result ||
                err?.message ||
                "Failed to run eval",
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
                label={ROW_TYPE_LABEL[rowType] || rowType}
                size="small"
                sx={{
                  height: 18,
                  fontSize: "10px",
                  bgcolor: "background.neutral",
                  color: "text.secondary",
                  "& .MuiChip-label": { px: 0.75 },
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
                fieldNames={fieldNames}
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
  // Top-level keys win deduplication (same logic as the fieldNames flatten).
  const entries = useMemo(() => {
    const raw = Object.entries(spanDetail).filter(([key]) => key !== "spans");
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
    for (const [k, v] of Object.entries(spanAttrs)) {
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
                <Typography
                  variant="caption"
                  fontWeight={500}
                  noWrap
                  sx={{ width: 130, flexShrink: 0, pt: 0.25 }}
                >
                  {key}
                </Typography>
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
  fieldNames,
  testResults = {},
}) => {
  const fieldSet = useMemo(() => new Set(fieldNames), [fieldNames]);
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
          color="text.disabled"
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
                    // Legacy mappings may still have the `span_attributes.` prefix;
                    // fieldSet is now soft-flattened, so check the stripped form too.
                    const strippedField =
                      typeof field === "string" &&
                      field.startsWith("span_attributes.")
                        ? field.slice("span_attributes.".length)
                        : field;
                    const resolved =
                      fieldSet.has(field) ||
                      (strippedField && fieldSet.has(strippedField));
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
                              color: resolved ? "primary.main" : "warning.main",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {field || "—"}
                          </Typography>
                        </CustomTooltip>
                        {!resolved && field && (
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
  fieldNames: PropTypes.array.isRequired,
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
