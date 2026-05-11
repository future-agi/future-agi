/* eslint-disable react/prop-types */
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  MenuItem,
  Select,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import DraggableColResizer from "src/components/draggable-col-resizer";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { PROJECT_SOURCE } from "src/utils/constants";
import {
  canonicalEntries,
  canonicalKeys,
  stripAttributePathPrefix,
} from "src/utils/utils";

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
import { JsonValueTree } from "./DatasetTestMode";
import { buildCompositeRuntimeConfig } from "../Helpers/compositeRuntimeConfig";
import EvalResultDisplay from "./EvalResultDisplay";
import useErrorLocalizerPoll from "../hooks/useErrorLocalizerPoll";
import { useExecuteCompositeEvalAdhoc } from "../hooks/useCompositeEval";

const ROW_TYPES = ["Span", "Trace", "Session"];

// Hover-tooltip content for the Columns / Value table. Stringifies
// primitives and JSON-encodes objects, then caps length so a 50k-char
// transcript doesn't blow up the tooltip.
const TOOLTIP_MAX = 4000;
function formatTooltipValue(val) {
  if (val === null || val === undefined) return "—";
  let text;
  if (typeof val === "string") text = val;
  else if (typeof val === "boolean" || typeof val === "number")
    text = String(val);
  else {
    try {
      text = JSON.stringify(val, null, 2);
    } catch {
      text = String(val);
    }
  }
  return text.length > TOOLTIP_MAX
    ? `${text.slice(0, TOOLTIP_MAX)}… (${text.length - TOOLTIP_MAX} more chars)`
    : text;
}

// Deep search: check if a value (including nested JSON keys/values) matches query
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

// Recursively find a span by ID in the observation spans tree.
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

// Flatten span tree into an ordered list (depth-first, like the graph)
// Flatten span tree into an ordered list with smart indexing.
// Each span gets: _depth, _index (global), _path (breadcrumb), _nameIndex (occurrence # for duplicate names)
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

      // Track per-name occurrence count
      nameCountMap[name] = (nameCountMap[name] || 0) + 1;
      const nameIndex = nameCountMap[name];

      // Build breadcrumb path
      const path = parentPath ? `${parentPath} › ${name}` : name;

      result.push({
        ...s,
        _depth: depth,
        _path: path,
        _nameIndex: nameIndex,
        _nameTotal: 0, // filled in second pass
      });

      if (item.children?.length) {
        result.push(
          ...flattenSpanTree(item.children, depth + 1, path, nameCountMap),
        );
      }
    }
  }

  // Second pass (root only): fill in _nameTotal so we know if # suffix is needed
  if (isRoot) {
    for (const span of result) {
      span._nameTotal = nameCountMap[span.name || "span"] || 1;
    }
  }

  return result;
}

/**
 * Tracing test mode for evals.
 *
 * 1. Pick a project
 * 2. Choose row type: Span / Trace / Session
 * 3. Browse paginated data with expandable JSON values
 * 4. Map template variables to data fields
 * 5. Run eval test
 */
// Normalize external row-type values (lowercase from task form) to the
// internal capitalized form this component uses. Voice is first-class
// so the dedicated voice_call_detail endpoint is used.
const normalizeRowType = (value) => {
  if (!value) return "Span";
  const v = String(value).toLowerCase();
  if (v === "span" || v === "spans") return "Span";
  if (v === "trace" || v === "traces") return "Trace";
  if (v === "session" || v === "sessions") return "Session";
  if (
    v === "voicecall" ||
    v === "voicecalls" ||
    v === "voice_calls" ||
    v === "voice"
  ) {
    return "VoiceCall";
  }
  return "Span";
};

const TracingTestMode = React.forwardRef(
  (
    {
      templateId,
      model = "turing_large",
      variables = [],
      codeParams = {},
      onTestResult,
      onColumnsLoaded,
      onClearResult,
      // Signals to EvalPickerConfigFull that all variables are mapped so
      // it can enable the Test Evaluation / Add Evaluation buttons.
      onReadyChange,
      // Optional: pre-select project + row type and hide the project picker
      // and the row type toggle. Used by the task flow's Add Evaluation
      // drawer so the user sees the exact same data their task will run on.
      initialProjectId = null,
      initialRowType = null,
      // Optional: seed the variable→field mapping (used when editing an
      // already-configured eval so the user's previous mapping is preserved).
      initialMapping = null,
      errorLocalizerEnabled = false,
      isComposite = false,
      compositeAdhocConfig = null,
      // Optional ad-hoc filters merged into the row-list `filters` param.
      localFilters = [],
    },
    ref,
  ) => {
    const projectLocked = !!initialProjectId;
    const rowTypeLocked = !!initialRowType;

    // Project
    const [projects, setProjects] = useState([]);
    const [loadingProjects, setLoadingProjects] = useState(false);
    const [selectedProjectId, setSelectedProjectId] = useState(
      initialProjectId || "",
    );

    // Row type
    const [rowType, setRowType] = useState(
      initialRowType ? normalizeRowType(initialRowType) : "Span",
    );

    // Project details fetched per selected project. The list_projects API
    // omits the `source` field, so we hit project-detail to know whether
    // the selected project is a voice/simulator project. Task flow relies
    // on the same detail fetch when the project is pre-selected.
    const [selectedProjectDetail, setSelectedProjectDetail] = useState(null);

    // Selected project object — prefer the detail fetch (has `source`) and
    // fall back to the list row so callers still get `name` etc. while the
    // detail request is in flight.
    const selectedProject = useMemo(() => {
      if (selectedProjectDetail) return selectedProjectDetail;
      if (projectLocked) return null;
      return (
        projects.find((p) => String(p.id) === String(selectedProjectId)) || null
      );
    }, [selectedProjectDetail, projectLocked, projects, selectedProjectId]);

    const isVoiceProject = selectedProject?.source === PROJECT_SOURCE.SIMULATOR;

    // Auto-switch rowType when the project type changes: voice projects
    // are always VoiceCall; switching back to a non-voice project falls
    // back to Span so the data table can populate with span rows.
    useEffect(() => {
      if (rowTypeLocked) return;
      if (!selectedProjectId) return;
      if (isVoiceProject && rowType !== "VoiceCall") {
        setRowType("VoiceCall");
      } else if (!isVoiceProject && rowType === "VoiceCall") {
        setRowType("Span");
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedProjectId, isVoiceProject]);

    // Data
    const [columns, setColumns] = useState([]);
    const [rows, setRows] = useState([]);
    const [totalRows, setTotalRows] = useState(0);
    const [currentRowIndex, setCurrentRowIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    // Key the last-completed fetch so we can derive "is the current
    // selection stale w.r.t. the last fetch" at render time. React
    // effects run *after* paint, so tracking a `hasFetched` boolean
    // still left a render-frame gap where the empty state flashed and
    // the spinner appeared late. Comparing `selectedProjectId:rowType`
    // against the last-fetched key tells us synchronously — in the same
    // render that the props changed — that new data is on the way.
    const [lastFetchedKey, setLastFetchedKey] = useState(null);
    const currentFetchKey = selectedProjectId
      ? `${selectedProjectId}:${rowType}`
      : null;
    const isPendingNewFetch =
      !!currentFetchKey && lastFetchedKey !== currentFetchKey;

    // Columns/Value table — user-resizable key column. Drag the divider
    // between key and value to widen long dotted paths. Ref holds the
    // live width during drag so the mousemove handler reads the
    // current value without recreating handlers.
    const [keyColWidth, setKeyColWidth] = useState(130);
    const keyColWidthRef = useRef(130);
    useEffect(() => {
      keyColWidthRef.current = keyColWidth;
    }, [keyColWidth]);

    // Span/trace detail (full attributes)
    const [spanDetail, setSpanDetail] = useState(null);
    const [loadingDetail, setLoadingDetail] = useState(false);

    // Per-row cache so toggling rows doesn't refetch the trace or re-walk
    // the response. Keyed by `${rowType}:${traceId}[:${spanId}]`. Each entry
    // is `{ detail, fieldNames? }` — fieldNames is filled lazily on first
    // walk and reused on subsequent row toggles.
    const detailCacheRef = useRef(new Map());

    // Table display
    const [tableSearch, setTableSearch] = useState("");
    const [expandedCols, setExpandedCols] = useState({});

    // Variable mapping
    const [mapping, setMapping] = useState(() =>
      initialMapping && typeof initialMapping === "object"
        ? { ...initialMapping }
        : {},
    );

    // Template ID ref (updated via imperative handle for first-test flow)
    const templateIdRef = useRef(templateId);
    useEffect(() => {
      templateIdRef.current = templateId;
    }, [templateId]);

    // Eval result
    const [isRunning, setIsRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    // Async error localization poll — see DatasetTestMode for rationale.
    const { state: errorLocalizerState, start: startErrorLocalizerPoll } =
      useErrorLocalizerPoll();
    const executeCompositeAdhoc = useExecuteCompositeEvalAdhoc();

    // ── Fetch project list (skip when project is pre-selected/locked) ──
    useEffect(() => {
      if (projectLocked) return;
      const fetchProjects = async () => {
        setLoadingProjects(true);
        try {
          const { data } = await axios.get(endpoints.project.listProjects(), {
            params: { project_type: "observe" },
          });
          const items = data?.result?.projects || data?.result || [];
          setProjects(Array.isArray(items) ? items : []);
        } catch {
          setProjects([]);
        } finally {
          setLoadingProjects(false);
        }
      };
      fetchProjects();
    }, [projectLocked]);

    // Fetch project detail whenever the selection changes. The list_projects
    // API doesn't include `source`, so without this the user-picked path
    // would never detect voice projects. Also covers the task-flow path
    // where the list fetch is skipped entirely.
    useEffect(() => {
      const pid = projectLocked ? initialProjectId : selectedProjectId;
      if (!pid) {
        setSelectedProjectDetail(null);
        return undefined;
      }
      let cancelled = false;
      (async () => {
        try {
          const { data } = await axios.get(
            endpoints.project.getProjectById(pid),
          );
          if (cancelled) return;
          const detail = data?.result || data || null;
          setSelectedProjectDetail(detail);
        } catch {
          if (!cancelled) setSelectedProjectDetail(null);
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [projectLocked, initialProjectId, selectedProjectId]);

    // ── Fetch data when project or rowType changes ──
    useEffect(() => {
      if (!selectedProjectId) {
        setColumns([]);
        setRows([]);
        setTotalRows(0);
        setCurrentRowIndex(0);
        setLastFetchedKey(null);
        return;
      }

      // Flip loading synchronously so the spinner shows while the fetch
      // is in flight. Empty-state visibility comes from the render-time
      // `isPendingNewFetch` comparison, not a `hasFetched` state.
      setLoading(true);

      const fetchData = async () => {
        setRows([]);
        try {
          if (rowType === "VoiceCall") {
            const { data } = await axios.get(endpoints.project.getCallLogs, {
              params: {
                project_id: selectedProjectId,
                page: 1,
                page_size: 50,
                filters: JSON.stringify(localFilters || []),
              },
            });
            const result = data?.result || data || {};
            const rowsOut = result.results || result.data || result.calls || [];
            setColumns([]);
            setRows(rowsOut);
            setTotalRows(result.total_count || result.total || rowsOut.length);
            setCurrentRowIndex(0);
            return;
          }

          let endpoint;
          const params = {
            project_id: selectedProjectId,
            page_number: 0,
            page_size: 50,
            filters: JSON.stringify(localFilters || []),
            interval: "year",
          };

          if (rowType === "Span") {
            endpoint = endpoints.project.getSpansForObserveProject();
          } else if (rowType === "Trace") {
            endpoint = endpoints.project.getTracesForObserveProject();
          } else {
            endpoint = endpoints.project.projectSessionList();
          }

          const { data } = await axios.get(endpoint, { params });
          const res = data?.result || {};

          // API returns { config, table, metadata }
          const cols = res.config || [];
          const tableRows = res.table || [];
          const total = res.metadata?.total_rows || tableRows.length;

          setColumns(cols);
          setRows(tableRows);
          setTotalRows(total);
          setCurrentRowIndex(0);
        } catch {
          setColumns([]);
          setRows([]);
          setTotalRows(0);
        } finally {
          setLoading(false);
          setLastFetchedKey(`${selectedProjectId}:${rowType}`);
        }
      };

      fetchData();
      // Stringify so the dep compares by value, not reference.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedProjectId, rowType, JSON.stringify(localFilters || [])]);

    // ── Current row ──
    const currentRow = rows[currentRowIndex] || null;

    // ── Fetch full span/trace detail when row changes ──
    useEffect(() => {
      if (!currentRow) {
        setSpanDetail(null);
        return;
      }

      const spanId = currentRow.span_id;
      const traceId = currentRow.trace_id;
      const cacheKey =
        rowType === "Span"
          ? `Span:${traceId || ""}:${spanId || ""}`
          : `${rowType}:${traceId || spanId || ""}`;

      // Cache hit: reuse the exact same detailData reference so the
      // downstream fieldNames memo short-circuits too.
      const cached = detailCacheRef.current.get(cacheKey);
      if (cached) {
        setSpanDetail(cached.detail);
        setLoadingDetail(false);
        return;
      }

      const fetchDetail = async () => {
        setLoadingDetail(true);
        try {
          let detailData = null;

          // Voice → dedicated voice_call_detail endpoint (transcript,
          // recording URLs, scenario info, customer info, latency, etc.)
          if (rowType === "VoiceCall" && traceId) {
            try {
              const { data } = await axios.get(
                endpoints.project.getVoiceCallDetail,
                { params: { trace_id: traceId } },
              );
              const voiceResult = data?.result || data?.data || data || {};
              // Spread row-list fields first as a fallback so we never
              // lose data that was only present on the list row.
              detailData = { ...currentRow, ...voiceResult };
            } catch {
              detailData = { ...currentRow };
            }
          } else if ((rowType === "Span" || rowType === "Trace") && traceId) {
            // Fetch the TRACE detail — same API as the drawer uses.
            // This returns all observation spans with full attributes (including spanAttributes).
            const { data } = await axios.get(
              endpoints.project.getTrace(traceId),
            );
            const traceResult = data?.result;

            const spans = traceResult?.observation_spans;
            if (rowType === "Span" && spanId && spans) {
              detailData = findSpanInTree(spans, spanId);
              if (!detailData) {
                const firstSpan = spans?.[0];
                detailData = firstSpan?.observation_span || traceResult?.trace;
              }
            } else {
              const traceInfo = traceResult?.trace || {};
              const allSpans = flattenSpanTree(spans);
              detailData = {
                ...traceInfo,
                spans: allSpans,
              };
            }
          } else {
            // Sessions: use row data directly
            detailData = { ...currentRow };
          }

          detailCacheRef.current.set(cacheKey, { detail: detailData });
          setSpanDetail(detailData);
        } catch {
          setSpanDetail(null);
        } finally {
          setLoadingDetail(false);
        }
      };

      fetchDetail();
    }, [currentRow, currentRowIndex, rowType, columns]);

    // ── Extract displayable fields from current row ──
    const rowFields = useMemo(() => {
      if (!currentRow) return [];
      if (!columns.length) {
        // No column config — use all row keys directly. canonicalEntries
        // drops the camelCase aliases the axios interceptor attaches so
        // each backend field only appears once.
        return canonicalEntries(currentRow).map(([key, val]) => ({
          key,
          colId: key,
          value: val ?? "",
          raw: val,
        }));
      }
      return columns
        .filter((col) => {
          const name = col.name || col.headerName;
          return col.id && name && !["id", "org_id"].includes(col.id);
        })
        .map((col) => {
          const value = currentRow[col.id] ?? "";
          return {
            key: col.name || col.headerName || col.id,
            colId: col.id,
            value: value != null ? value : "",
            raw: value,
          };
        });
    }, [currentRow, columns]);

    // ── Attribute names for variable mapping dropdown ──
    // Expand nested object keys into dot-notation paths (e.g. input.role,
    // metadata.name). Soft-flatten: attributes inside `span_attributes.*`
    // are surfaced as bare names (e.g. `input` instead of
    // `span_attributes.input`) so users can map variables to short,
    // short field names. Top-level fields with the same name
    // win the deduplication. The resolver below transparently falls back
    // to `span_attributes.<name>` when the top-level lookup misses, so
    // legacy mappings that already stored the full `span_attributes.`
    // prefix continue to work unchanged.
    // Walk the detail payload into dot-notation paths. Split from
    // `fieldNames` below so the expensive recursion only re-runs when the
    // `spanDetail` reference actually changes — navigating rows that share
    // a cache entry returns the same `spanDetail` and skips the walk. Per-
    // trace walked output is also memoised back into `detailCacheRef` so a
    // cross-row bounce gets the same list without re-walking.
    const walkedFromDetail = useMemo(() => {
      const source = spanDetail || null;
      if (!source) return null;

      // Reuse previously walked output for this detail reference if we
      // have it — avoids rewalking when React reuses the same cached
      // detailData object across row toggles.
      for (const entry of detailCacheRef.current.values()) {
        if (entry.detail === source && entry.fieldNames) {
          return entry.fieldNames;
        }
      }

      const keys = [];
      // Walks both dicts and arrays. Array elements get numeric
      // indices (e.g. `messages.0.content`) so users can target
      // individual items in chat-message lists. Limits prevent
      // runaway recursion: 5000 dict keys, 500 array elements.
      const ARRAY_PEEK = 500;
      const DICT_LIMIT = 5000;
      // Subtrees we deliberately don't recurse into — the key itself
      // stays selectable, but its (often multi-MB) children are skipped
      // so the first-row walk finishes in tens of ms on voice traces.
      // `raw_log` is the Vapi call dump, `metrics_data` / `call_logs`
      // are the per-turn payloads, `provider_transcript` is the raw
      // transcript string — none of these are useful as variable paths.
      const NO_RECURSE_KEYS = new Set([
        "raw_log",
        "rawLog",
        "metrics_data",
        "metricsData",
        "call_logs",
        "callLogs",
        "provider_transcript",
        "providerTranscript",
      ]);
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
        // canonicalEntries strips the camelCase aliases the axios
        // interceptor layers on top of snake_case fields, otherwise the
        // attribute autocomplete dropdown lists every path twice.
        for (const [k, v] of canonicalEntries(node)) {
          if (k.startsWith("_")) continue;
          const path = prefix ? `${prefix}.${k}` : k;
          keys.push(path);
          if (NO_RECURSE_KEYS.has(k)) continue;
          if (v && typeof v === "object") {
            if (Array.isArray(v) || canonicalKeys(v).length < DICT_LIMIT) {
              walk(v, path);
            }
          }
        }
      };
      walk(source, "");
      // Strip wrapper/span_attributes prefix and dedupe against top-level keys.
      const seen = new Set();
      const flattened = [];
      keys.forEach((k) => {
        const short = stripAttributePathPrefix(k);
        if (seen.has(short)) return;
        seen.add(short);
        flattened.push(short);
      });

      // Persist back into the per-row cache so the next row toggle that
      // resolves to this same detail reference short-circuits the walk.
      for (const [key, entry] of detailCacheRef.current.entries()) {
        if (entry.detail === source) {
          detailCacheRef.current.set(key, { ...entry, fieldNames: flattened });
          break;
        }
      }

      return flattened;
    }, [spanDetail]);

    const fieldNames = useMemo(
      () => walkedFromDetail || rowFields.map((f) => f?.colId || f?.key),
      [walkedFromDetail, rowFields],
    );

    // Notify parent of available fields for autocomplete
    useEffect(() => {
      if (fieldNames.length > 0 && onColumnsLoaded) {
        const cols = fieldNames.map((k) => ({
          id: k,
          name: k,
          dataType: "text",
        }));
        onColumnsLoaded(cols, {});
      }
    }, [fieldNames.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

    // Auto-map variables to fields when names match
    useEffect(() => {
      if (!fieldNames.length || !variables.length) return;
      const fieldSet = new Set(fieldNames);
      setMapping((prev) => {
        const next = { ...prev };
        let changed = false;
        variables.forEach((v) => {
          // Normalize legacy `span_attributes.X` values to the soft-flattened
          // form when X now exists in the dropdown — otherwise the Select
          // renders blank because the stored value has no matching MenuItem.
          const existing = next[v];
          if (
            typeof existing === "string" &&
            existing.startsWith("span_attributes.")
          ) {
            const stripped = existing.slice("span_attributes.".length);
            if (fieldSet.has(stripped)) {
              next[v] = stripped;
              changed = true;
              return;
            }
          }
          if (next[v]) return;
          const exact = fieldNames.find((f) => f === v);
          const ci =
            !exact &&
            fieldNames.find((f) => f.toLowerCase() === v.toLowerCase());
          const match = exact || ci;
          if (match) {
            next[v] = match;
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }, [variables, fieldNames]);

    // Signal ready state to parent: ready when every template variable
    // has a non-empty mapped field AND we have a current row to test
    // against. This enables the Test Evaluation / Add Evaluation buttons
    // in EvalPickerConfigFull.
    useEffect(() => {
      if (!onReadyChange) return;
      // Evals with zero variables (e.g. some code evals) are always ready
      // as long as we have a loaded row to run against.
      const allMapped =
        variables.length === 0 ||
        variables.every((v) => mapping[v] && String(mapping[v]).length > 0);
      const hasRow = !!currentRow;
      onReadyChange(allMapped && hasRow, mapping);
    }, [variables, mapping, currentRow, onReadyChange]);

    // ── Run test ──
    const handleRunTest = useCallback(async () => {
      const tid = templateIdRef.current;
      if (!tid) {
        onTestResult?.(false, "No template ID — save the eval first");
        return;
      }
      setIsRunning(true);
      setResult(null);
      setError(null);

      if (!variables.length) {
        onTestResult?.(
          false,
          "No variables to map — eval template may still be loading",
        );
        setIsRunning(false);
        return;
      }
      if (!spanDetail) {
        onTestResult?.(
          false,
          "Span data not loaded yet — please wait and retry",
        );
        setIsRunning(false);
        return;
      }

      try {
        // Build a flat fieldName→value lookup by walking spanDetail with
        // the same logic used to populate the fieldNames dropdown. This
        // ensures that a field name selected from the dropdown (e.g.
        // "input.value" — which may have been soft-flattened from
        // "span_attributes.input.value") always resolves to the correct
        // value, even when the top-level key shadows a deeper path.
        // Limits match the dropdown walker so every path offered in the UI
        // also resolves — otherwise deep paths (e.g. gen_ai.* under a big
        // span_attributes dict) would be selectable but unresolvable.
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
          // canonicalEntries drops the camelCase aliases the axios
          // interceptor layers on — otherwise valueMap gets both
          // `span_attributes.*` and `spanAttributes.*` branches of the
          // same data, and only the snake side is stripped by the
          // soft-flatten below.
          for (const [k, v] of canonicalEntries(node)) {
            if (k.startsWith("_")) continue;
            const path = prefix ? `${prefix}.${k}` : k;
            valueMap[path] = v;
            if (v && typeof v === "object") {
              if (
                Array.isArray(v) ||
                Object.keys(v).length < DICT_LIMIT
              ) {
                walkValues(v, path);
              }
            }
          }
        };
        walkValues(spanDetail, "");

        // Build the soft-flattened lookup: strip `span_attributes.`
        // prefix (top-level keys win on collision, matching the
        // fieldNames dropdown behaviour).
        const flatValueMap = {};
        for (const [path, val] of Object.entries(valueMap)) {
          const short = path.startsWith("span_attributes.")
            ? path.slice("span_attributes.".length)
            : path;
          // Top-level keys take priority — only add span_attributes
          // paths when there is no existing top-level entry.
          if (!(short in flatValueMap) || !path.startsWith("span_attributes.")) {
            flatValueMap[short] = val;
          }
        }

        const evalMapping = {};
        for (const variable of variables) {
          const mappedField = mapping[variable];
          if (!mappedField) continue;

          // 1. Try the flat value map (same resolution as the dropdown)
          let val = flatValueMap[mappedField];

          // 2. Fallback: rowFields by key or colId
          if (val === undefined) {
            const rf = rowFields.find(
              (f) => f.key === mappedField || f.colId === mappedField,
            );
            if (rf?.raw !== undefined && rf.raw !== null) {
              val = rf.raw;
            }
          }

          if (val !== undefined) {
            evalMapping[variable] =
              typeof val === "object"
                ? JSON.stringify(val)
                : String(val ?? "");
          }
        }

        // Single-eval playground resolves {{span}} / {{trace}} /
        // {{session}} server-side from IDs. Composite execution expects
        // the concrete context objects directly.
        const autoCtx = {};
        const _spanId = currentRow?.span_id || currentRow?.spanId;
        const _traceId = currentRow?.trace_id || currentRow?.traceId;
        const _sessionId = currentRow?.session_id || currentRow?.sessionId;
        if (rowType === "Span" && _spanId) autoCtx.span_id = _spanId;
        if ((rowType === "Span" || rowType === "Trace") && _traceId)
          autoCtx.trace_id = _traceId;
        if (rowType === "Session" && _sessionId)
          autoCtx.session_id = _sessionId;
        if (rowType === "VoiceCall" && _traceId) autoCtx.trace_id = _traceId;

        const compositeCtx = {};
        if (rowType === "Span" && spanDetail) compositeCtx.span_context = spanDetail;
        if (rowType === "Trace" && currentRow)
          compositeCtx.trace_context = currentRow;
        if (rowType === "Session" && currentRow)
          compositeCtx.session_context = currentRow;
        if (rowType === "VoiceCall" && currentRow)
          compositeCtx.trace_context = currentRow;

        const compositeConfig = buildCompositeRuntimeConfig({
          codeParams,
        });

        const { data } = isComposite
          ? compositeAdhocConfig
            ? {
                data: {
                  status: true,
                  result: await executeCompositeAdhoc.mutateAsync({
                    ...compositeAdhocConfig,
                    mapping: evalMapping,
                    model,
                    error_localizer: errorLocalizerEnabled,
                    config: compositeConfig,
                    ...compositeCtx,
                  }),
                },
              }
            : await axios.post(endpoints.develop.eval.executeCompositeEval(tid), {
                mapping: evalMapping,
                model,
                error_localizer: errorLocalizerEnabled,
                config: compositeConfig,
                ...compositeCtx,
              })
          : await axios.post(endpoints.develop.eval.evalPlayground, {
              template_id: tid,
              model,
              error_localizer: errorLocalizerEnabled,
              config: {
                mapping: evalMapping,
                ...(Object.keys(codeParams || {}).length > 0
                  ? { params: codeParams }
                  : {}),
              },
              ...autoCtx,
            });

        if (data?.status) {
          const nextResult = isComposite
            ? {
                output:
                  data.result?.aggregation_enabled &&
                  data.result?.aggregate_score != null
                    ? data.result.aggregate_score
                    : null,
                reason: data.result?.summary || "",
                compositeResult: data.result,
              }
            : data.result;
          setResult(nextResult);
          onTestResult?.(true, nextResult);
          if (!isComposite && errorLocalizerEnabled && data.result?.log_id) {
            startErrorLocalizerPoll(data.result.log_id);
          }
        } else {
          const errMsg = data?.result || "Evaluation failed";
          setError(errMsg);
          onTestResult?.(false, errMsg);
        }
      } catch (err) {
        const errMsg =
          err?.result ||
          err?.detail ||
          err?.message ||
          "Failed to run evaluation";
        setError(errMsg);
        onTestResult?.(false, errMsg);
      } finally {
        setIsRunning(false);
      }
    }, [
      templateId,
      variables,
      mapping,
      spanDetail,
      rowFields,
      currentRow,
      rowType,
      onTestResult,
      errorLocalizerEnabled,
      isComposite,
      compositeAdhocConfig,
      startErrorLocalizerPoll,
      codeParams,
      model,
      executeCompositeAdhoc,
    ]);

    useImperativeHandle(
      ref,
      () => ({
        runTest: (overrideTemplateId) => {
          if (overrideTemplateId) templateIdRef.current = overrideTemplateId;
          handleRunTest();
        },
      }),
      [handleRunTest],
    );

    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {/* Project selector — hidden when pre-selected (e.g. task flow) */}
        {!projectLocked && (
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Project
              <Typography component="span" sx={{ color: "error.main" }}>
                *
              </Typography>
            </Typography>
            <Autocomplete
              size="small"
              options={projects}
              getOptionLabel={(opt) => opt?.name || opt?.id || ""}
              value={projects.find((p) => p.id === selectedProjectId) || null}
              onChange={(_, val) => setSelectedProjectId(val?.id || "")}
              loading={loadingProjects}
              openOnFocus
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder="Search projects..."
                  InputProps={{
                    ...params.InputProps,
                    sx: { ...params.InputProps.sx, fontSize: "13px" },
                    endAdornment: loadingProjects ? (
                      <InputAdornment position="end">
                        <CircularProgress size={14} />
                      </InputAdornment>
                    ) : (
                      params.InputProps.endAdornment
                    ),
                  }}
                />
              )}
              renderOption={(props, option) => {
                const { key, ...rest } = props;
                return (
                  <Box
                    component="li"
                    key={key}
                    {...rest}
                    sx={{ ...rest.sx, fontSize: "13px" }}
                  >
                    {option.name || option.id}
                  </Box>
                );
              }}
              ListboxProps={{ style: { maxHeight: 250 } }}
            />
          </Box>
        )}

        {/* Voice indicator — voice projects always map to voice calls, so
            the row-type tabs are replaced by a static chip that mirrors the
            "Voice Calls" label shown in the task flow's live preview. */}
        {!rowTypeLocked && !!selectedProjectId && isVoiceProject && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
            <Typography variant="body2" fontWeight={600}>
              Row Type
            </Typography>
            <Chip
              label="Voice Calls"
              size="small"
              sx={{
                height: 20,
                fontSize: "11px",
                bgcolor: "background.neutral",
                color: "text.secondary",
                "& .MuiChip-label": { px: 0.75 },
                "& .MuiChip-icon": { ml: 0.5, mr: -0.25 },
              }}
            />
          </Box>
        )}

        {/* Row type toggle — hidden when:
            - row type is pre-set by parent (task flow)
            - no project selected yet (nothing to type against)
            - selected project is a voice/simulator project (always
              VoiceCall, row type isn't meaningful) */}
        {!rowTypeLocked && !!selectedProjectId && !isVoiceProject && (
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Row Type
              <Typography component="span" sx={{ color: "error.main" }}>
                *
              </Typography>
            </Typography>
            <Tabs
              value={rowType}
              onChange={(_, val) => setRowType(val)}
              variant="standard"
              scrollButtons={false}
              TabIndicatorProps={{ style: { display: "none" } }}
              sx={{
                minHeight: 28,
                "& .MuiTabs-scroller": { overflow: "visible !important" },
                "& .MuiTab-root": {
                  minHeight: 28,
                  px: 1.5,
                  py: 0,
                  mr: "0px !important",
                  textTransform: "none",
                  fontSize: "12px",
                  borderRadius: "6px",
                },
                border: "1px solid",
                borderColor: "divider",
                p: "2px",
                borderRadius: "8px",
                width: "fit-content",
                bgcolor: (theme) =>
                  theme.palette.mode === "dark"
                    ? "rgba(255,255,255,0.04)"
                    : "background.neutral",
              }}
            >
              {ROW_TYPES.map((t) => (
                <Tab
                  key={t}
                  value={t}
                  label={t}
                  sx={{
                    bgcolor:
                      rowType === t
                        ? (theme) =>
                            theme.palette.mode === "dark"
                              ? "rgba(255,255,255,0.12)"
                              : "background.paper"
                        : "transparent",
                    boxShadow:
                      rowType === t
                        ? (theme) =>
                            theme.palette.mode === "dark"
                              ? "none"
                              : "0 1px 3px rgba(0,0,0,0.08)"
                        : "none",
                    borderRadius: "6px",
                    fontWeight: rowType === t ? 600 : 400,
                    color: rowType === t ? "text.primary" : "text.disabled",
                  }}
                />
              ))}
            </Tabs>
          </Box>
        )}

        {/* Loading */}
        {(loading || isPendingNewFetch) && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}

        {/* Row navigator */}
        {selectedProjectId &&
          (rows?.length ?? 0) > 0 &&
          !loading &&
          !isPendingNewFetch && (
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
              Row {Math.min(currentRowIndex + 1, rows?.length ?? 0)} of{" "}
              {rows?.length ?? 0}
              {(totalRows ?? 0) > (rows?.length ?? 0) && (
                <Typography
                  component="span"
                  sx={{
                    fontSize: "11px",
                    color: "text.disabled",
                    ml: 0.5,
                  }}
                >
                  ({totalRows} matching total)
                </Typography>
              )}
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <IconButton
                size="small"
                disabled={currentRowIndex === 0}
                onClick={() => {
                  setCurrentRowIndex((i) => Math.max(0, i - 1));
                  setResult(null);
                  setError(null);
                  onClearResult?.();
                }}
                sx={{ width: 24, height: 24 }}
              >
                <Iconify icon="mdi:chevron-left" width={16} />
              </IconButton>
              <IconButton
                size="small"
                disabled={currentRowIndex >= (rows?.length ?? 0) - 1}
                onClick={() => {
                  setCurrentRowIndex((i) =>
                    Math.min((rows?.length ?? 0) - 1, i + 1),
                  );
                  setResult(null);
                  setError(null);
                  onClearResult?.();
                }}
                sx={{ width: 24, height: 24 }}
              >
                <Iconify icon="mdi:chevron-right" width={16} />
              </IconButton>
            </Box>
          </Box>
        )}

        {/* Span/Trace detail — table format like DatasetTestMode */}
        {loadingDetail && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={18} />
          </Box>
        )}

        {spanDetail && !loadingDetail && (
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

            {/* Rows — iterate span detail keys, flatten span_attributes */}
            <Box sx={{ maxHeight: 400, overflowY: "auto" }}>
              {(() => {
                // canonicalEntries skips the camelCase aliases the axios
                // interceptor adds — otherwise every field shows up twice
                // in the span detail table.
                const raw = canonicalEntries(spanDetail).filter(
                  ([key]) => key !== "spans",
                );
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
              })()
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

                  // Audio detection — voice calls surface recording URLs
                  // as direct fields (recording_url, stereo_recording_url,
                  // audio_url …) and a nested `recording` object with
                  // per-track URLs. Render playable audio inline.
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
                      <Tooltip
                        title={key}
                        placement="top-start"
                        enterDelay={300}
                        arrow
                      >
                        <Typography
                          variant="caption"
                          fontWeight={500}
                          noWrap
                          sx={{
                            width: keyColWidth,
                            flexShrink: 0,
                            pt: 0.25,
                          }}
                        >
                          {key}
                        </Typography>
                      </Tooltip>
                      <DraggableColResizer
                        getCurrentWidth={() => keyColWidthRef.current}
                        onResize={setKeyColWidth}
                        minWidth={80}
                        maxWidth={600}
                      />
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
                          <Tooltip
                            title={
                              <Box
                                component="span"
                                sx={{
                                  display: "block",
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-all",
                                  fontFamily: "monospace",
                                  fontSize: 11,
                                  maxWidth: 520,
                                }}
                              >
                                {formatTooltipValue(val)}
                              </Box>
                            }
                            placement="top-start"
                            enterDelay={300}
                            arrow
                          >
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
                              {/* Defensive: this branch should only be
                                  reached for primitives (the upstream
                                  isObj/isArr check routes objects to
                                  JsonValueTree). If something slips
                                  through, JSON.stringify rather than
                                  falling back to "[object Object]". */}
                              {typeof val === "boolean"
                                ? String(val)
                                : typeof val === "string"
                                  ? `"${val}"`
                                  : val !== null && typeof val === "object"
                                    ? JSON.stringify(val)
                                    : String(val)}
                            </Typography>
                          </Tooltip>
                        )}
                      </Box>
                    </Box>
                  );
                })}

              {/* Spans section (Trace mode) — each span as a collapsible row */}
              {spanDetail.spans?.map((span, idx) => {
                const spanKey = `span-${span.id || idx}`;
                const depth = span._depth || 0;
                const indent = depth * 16;
                const spanName = span.name || span.span_name || "span";
                const spanType = span.observation_type || "";
                const isExpanded = expandedCols[spanKey];
                const hasDuplicateName = (span._nameTotal || 1) > 1;
                const nameLabel = hasDuplicateName
                  ? `${spanName} #${span._nameIndex}`
                  : spanName;

                return (
                  <Box key={spanKey}>
                    {/* Span header row */}
                    <Box
                      onClick={() =>
                        setExpandedCols((prev) => ({
                          ...prev,
                          [spanKey]: !prev[spanKey],
                        }))
                      }
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        px: 1.5,
                        py: 0.75,
                        pl: `${12 + indent}px`,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                        cursor: "pointer",
                        backgroundColor: (theme) =>
                          theme.palette.mode === "dark"
                            ? "rgba(124,77,255,0.04)"
                            : "rgba(124,77,255,0.02)",
                        "&:hover": { backgroundColor: "action.hover" },
                        gap: 0.75,
                      }}
                    >
                      <Iconify
                        icon={
                          isExpanded ? "mdi:chevron-down" : "mdi:chevron-right"
                        }
                        width={14}
                        sx={{ color: "text.disabled", flexShrink: 0 }}
                      />
                      {/* Global index badge */}
                      <Box
                        sx={{
                          minWidth: 18,
                          height: 18,
                          borderRadius: "4px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          backgroundColor: (theme) =>
                            theme.palette.mode === "dark"
                              ? "rgba(255,255,255,0.08)"
                              : "rgba(0,0,0,0.06)",
                          flexShrink: 0,
                        }}
                      >
                        <Typography
                          sx={{
                            fontSize: "10px",
                            fontWeight: 700,
                            color: "text.secondary",
                          }}
                        >
                          {idx + 1}
                        </Typography>
                      </Box>
                      {/* Type icon */}
                      <Iconify
                        icon={
                          spanType === "GENERATION"
                            ? "mdi:creation"
                            : spanType === "TOOL"
                              ? "mdi:wrench"
                              : spanType === "RETRIEVAL"
                                ? "mdi:database-search"
                                : "mdi:layers-outline"
                        }
                        width={14}
                        sx={{
                          color:
                            spanType === "GENERATION"
                              ? "primary.main"
                              : spanType === "TOOL"
                                ? "warning.main"
                                : spanType === "RETRIEVAL"
                                  ? "info.main"
                                  : "text.secondary",
                          flexShrink: 0,
                        }}
                      />
                      {/* Name with # suffix for duplicates */}
                      <Typography
                        variant="caption"
                        fontWeight={600}
                        sx={{ fontSize: "12px" }}
                        noWrap
                      >
                        {nameLabel}
                      </Typography>
                      {/* Model chip */}
                      {span.model && (
                        <Box
                          sx={{
                            px: 0.75,
                            py: 0.1,
                            borderRadius: "4px",
                            flexShrink: 0,
                            backgroundColor: (theme) =>
                              theme.palette.mode === "dark"
                                ? "rgba(255,255,255,0.06)"
                                : "rgba(0,0,0,0.04)",
                          }}
                        >
                          <Typography
                            sx={{
                              fontSize: "10px",
                              color: "text.secondary",
                              fontFamily: "monospace",
                            }}
                          >
                            {span.model}
                          </Typography>
                        </Box>
                      )}
                      {/* Status */}
                      {span.status && span.status !== "OK" && (
                        <Box
                          sx={(t) => ({
                            px: 0.5,
                            py: 0.1,
                            borderRadius: "4px",
                            // error.lighter is a fixed light pink that looks
                            // out of place in dark mode; use a theme-reactive
                            // tint derived from error.main.
                            backgroundColor:
                              span.status === "ERROR"
                                ? alpha(
                                    t.palette.error.main,
                                    t.palette.mode === "dark" ? 0.16 : 0.08,
                                  )
                                : "transparent",
                          })}
                        >
                          <Typography
                            sx={{
                              fontSize: "9px",
                              fontWeight: 600,
                              color:
                                span.status === "ERROR"
                                  ? "error.main"
                                  : "text.disabled",
                            }}
                          >
                            {span.status}
                          </Typography>
                        </Box>
                      )}
                      {/* Tokens */}
                      {span.total_tokens > 0 && (
                        <Typography
                          sx={{ fontSize: "10px", color: "text.disabled" }}
                        >
                          {span.total_tokens}tok
                        </Typography>
                      )}
                      {/* Latency — pushed to right */}
                      {span.latency_ms != null && (
                        <Typography
                          sx={{
                            fontSize: "10px",
                            color: "text.disabled",
                            ml: "auto",
                            flexShrink: 0,
                          }}
                        >
                          {span.latency_ms}ms
                        </Typography>
                      )}
                    </Box>

                    {/* Expanded span attributes */}
                    {isExpanded && (
                      <Box
                        sx={{
                          pl: `${24 + indent}px`,
                          borderBottom: "1px solid",
                          borderColor: "divider",
                        }}
                      >
                        {sortEntries(
                          canonicalEntries(span).filter(
                            ([k]) => !k.startsWith("_"),
                          ),
                        )
                          .filter(([k, v]) => {
                            if (!tableSearch.trim()) return true;
                            const q = tableSearch.toLowerCase();
                            return (
                              k.toLowerCase().includes(q) || deepMatch(v, q)
                            );
                          })
                          .map(([k, v]) => {
                            const isO =
                              v !== null &&
                              v !== undefined &&
                              typeof v === "object";
                            const emp =
                              v === null ||
                              v === undefined ||
                              v === "" ||
                              (isO &&
                                !Array.isArray(v) &&
                                Object.keys(v).length === 0);
                            return (
                              <Box
                                key={k}
                                sx={{
                                  display: "flex",
                                  alignItems: "flex-start",
                                  px: 1.5,
                                  py: 0.4,
                                  "&:hover": {
                                    backgroundColor: "action.hover",
                                  },
                                }}
                              >
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  noWrap
                                  sx={{
                                    width: 120,
                                    flexShrink: 0,
                                    pt: 0.15,
                                    fontSize: "11px",
                                  }}
                                >
                                  {k}
                                </Typography>
                                <Box
                                  sx={{
                                    flex: 1,
                                    minWidth: 0,
                                    overflow: "hidden",
                                  }}
                                >
                                  {emp ? (
                                    <Typography
                                      variant="caption"
                                      color="text.disabled"
                                      sx={{ fontSize: "11px" }}
                                    >
                                      —
                                    </Typography>
                                  ) : isO ? (
                                    <JsonValueTree
                                      value={v}
                                      expanded={expandedCols[`${spanKey}-${k}`]}
                                      onToggle={() =>
                                        setExpandedCols((prev) => ({
                                          ...prev,
                                          [`${spanKey}-${k}`]:
                                            !prev[`${spanKey}-${k}`],
                                        }))
                                      }
                                    />
                                  ) : (
                                    <Typography
                                      variant="caption"
                                      color="primary.main"
                                      sx={{
                                        fontSize: "11px",
                                        wordBreak: "break-all",
                                      }}
                                    >
                                      {typeof v === "boolean"
                                        ? String(v)
                                        : typeof v === "string"
                                          ? `"${v}"`
                                          : String(v)}
                                    </Typography>
                                  )}
                                </Box>
                              </Box>
                            );
                          })}
                      </Box>
                    )}
                  </Box>
                );
              })}
            </Box>
          </Box>
        )}

        {/* Empty state */}
        {selectedProjectId &&
          !loading &&
          !isPendingNewFetch &&
          totalRows === 0 && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 0.75,
              py: 3,
              border: "1px dashed",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Iconify
              icon="mdi:table-off"
              width={28}
              sx={{ color: "text.disabled" }}
            />
            <Typography variant="body2" fontWeight={600} color="text.secondary">
              No {rowType.toLowerCase()} data found
            </Typography>
            <Typography variant="caption" color="text.disabled">
              Add {rowType.toLowerCase()} to this project before running a test
            </Typography>
          </Box>
        )}

        {/* Variable mapping */}
        {variables.length > 0 && (
          <Box>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ mb: 0.5, display: "block" }}
            >
              Variable Mapping
            </Typography>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
              {variables.map((variable) => (
                <Box
                  key={variable}
                  sx={{ display: "flex", alignItems: "center", gap: 1 }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      px: 1,
                      py: 0.25,
                      borderRadius: "4px",
                      border: "1px solid",
                      borderColor: "divider",
                      minWidth: 120,
                    }}
                  >
                    <Iconify
                      icon="mdi:code-braces"
                      width={14}
                      sx={{ color: "text.secondary" }}
                    />
                    <Typography
                      variant="caption"
                      fontWeight={600}
                      sx={{ fontSize: "12px" }}
                    >
                      {variable}
                    </Typography>
                  </Box>
                  <Iconify
                    icon="mdi:arrow-right"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                  <Autocomplete
                    size="small"
                    options={
                      mapping[variable] &&
                      !fieldNames.includes(mapping[variable])
                        ? [mapping[variable], ...fieldNames]
                        : fieldNames
                    }
                    value={mapping[variable] || null}
                    onChange={(_, val) =>
                      setMapping((prev) => ({
                        ...prev,
                        [variable]: val || "",
                      }))
                    }
                    openOnFocus
                    autoHighlight
                    selectOnFocus
                    handleHomeEndKeys
                    isOptionEqualToValue={(opt, val) => opt === val}
                    sx={{ flex: 1 }}
                    ListboxProps={{ style: { maxHeight: 260 } }}
                    renderInput={(params) => (
                      <TextField
                        {...params}
                        placeholder="Search column..."
                        InputProps={{
                          ...params.InputProps,
                          sx: {
                            ...params.InputProps.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                            height: 28,
                            py: 0,
                          },
                        }}
                      />
                    )}
                    renderOption={(props, col) => {
                      const { key, ...rest } = props;
                      return (
                        <Box
                          component="li"
                          key={key}
                          {...rest}
                          title={col}
                          sx={{
                            ...rest.sx,
                            fontSize: "12px",
                            fontFamily: "monospace",
                            pl: col.includes(".")
                              ? `${12 + (col.split(".").length - 1) * 12}px`
                              : undefined,
                            color: col.includes(".")
                              ? "primary.main"
                              : "text.primary",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {col}
                        </Box>
                      );
                    }}
                  />
                </Box>
              ))}
            </Box>
          </Box>
        )}

        {/* Result */}
        {result && (
          <EvalResultDisplay
            result={{
              ...result,
              ...(errorLocalizerState.status
                ? { error_localizer_status: errorLocalizerState.status }
                : {}),
              ...(errorLocalizerState.details
                ? {
                    error_details:
                      errorLocalizerState.details.error_analysis ||
                      errorLocalizerState.details,
                    selected_input_key:
                      errorLocalizerState.details.selected_input_key,
                    input_data: errorLocalizerState.details.input_data,
                    input_types: errorLocalizerState.details.input_types,
                  }
                : {}),
            }}
          />
        )}

        {error && (
          <Box
            sx={(t) => ({
              p: 1.5,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: alpha(t.palette.error.main, 0.4),
              backgroundColor: alpha(
                t.palette.error.main,
                t.palette.mode === "dark" ? 0.16 : 0.08,
              ),
            })}
          >
            <Typography variant="caption" color="error.main">
              {typeof error === "string" ? error : JSON.stringify(error)}
            </Typography>
          </Box>
        )}
      </Box>
    );
  },
);

TracingTestMode.displayName = "TracingTestMode";

TracingTestMode.propTypes = {
  templateId: PropTypes.string,
  variables: PropTypes.array,
  codeParams: PropTypes.object,
  onTestResult: PropTypes.func,
  onColumnsLoaded: PropTypes.func,
  onClearResult: PropTypes.func,
  onReadyChange: PropTypes.func,
  initialProjectId: PropTypes.string,
  initialRowType: PropTypes.string,
  initialMapping: PropTypes.object,
  isComposite: PropTypes.bool,
  compositeAdhocConfig: PropTypes.object,
  localFilters: PropTypes.array,
};

export default TracingTestMode;
