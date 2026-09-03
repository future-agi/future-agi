import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
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
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { canonicalEntries } from "src/utils/utils";
import { ROW_TYPE_LABELS } from "src/utils/constants";
import { executeEvalForRow } from "src/sections/evals/utils/evalExecution";
import {
  resolvePath,
  sortSpansForMapping,
} from "src/sections/evals/utils/rowPathWalker";
import {
  isMappingPath,
  mappingPathLabel,
} from "src/sections/evals/utils/evalMappingPath";
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
import { serializeFilterForApi } from "src/api/contracts/filter-contract";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { isTaskPreviewProjectKindReady } from "../taskProjectKind";
import {
  collectExactListRows,
  createListCursorProtocolError,
  isListCursorProtocolError,
  listCursorBoundaryIdentity,
  listContinuationParams,
  rememberBoundedListCursorIdentity,
  requestListWithLegacyCursorFallback,
} from "src/sections/projects/LLMTracing/listCursorPagination";
import {
  serializeTaskFilterRowsForApi,
  taskFilterColumnId,
} from "src/sections/common/EvalsTasks/task_filter_serialization";
import { QUERY_FAILED_RETRY_MESSAGE } from "src/utils/queryReadState";
import {
  parseAxiosResult,
  parseSessionObserveListResponse,
  parseSpanObserveListResponse,
  parseTraceObserveListResponse,
  parseVoiceCallDetailResponse,
  parseVoiceCallListResponse,
} from "src/api/project/observe-contracts";

// One form row → one wire entry. No cross-row merging: it would collapse
// "not_contains A AND not_contains B" into "in [A, B]" (inverting intent) and
// is unsupported for numbers (the BE has no number `in`). OR is expressed
// within a single multi-value `in`/`not_in` row.
// eslint-disable-next-line react-refresh/only-export-components
export function buildApiFilterArray(oldFormatFilters, startDate, endDate) {
  const userFilters = serializeTaskFilterRowsForApi(
    oldFormatFilters,
    (row) => ({
      omitColumnType: ID_ONLY_FIELDS.has(taskFilterColumnId(row)),
    }),
  );

  if (startDate && endDate) {
    userFilters.push(
      serializeFilterForApi({
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            new Date(startDate).toISOString(),
            new Date(endDate).toISOString(),
          ],
        },
      }),
    );
  }

  return userFilters;
}

const TASK_PREVIEW_CURSOR_ROW_TYPES = new Set([
  "voiceCalls",
  "traces",
  "spans",
  "sessions",
]);

const isTaskPreviewCursorRowType = (rowType) =>
  TASK_PREVIEW_CURSOR_ROW_TYPES.has(rowType);

// Trace/span/session/voice previews opt into signed bounded continuation and
// navigate one row at a time. Filling an eager 50-row preview could force many
// serial bounded scans before the first usable row rendered.
// eslint-disable-next-line react-refresh/only-export-components
export function buildTaskPreviewListParams({ rowType, projectId, apiFilters }) {
  const cursorCapable = isTaskPreviewCursorRowType(rowType);
  return {
    project_id: projectId,
    ...(rowType === "voiceCalls" ? { page: 1 } : { page_number: 0 }),
    page_size: cursorCapable ? 1 : 50,
    filters: JSON.stringify(apiFilters),
    ...(cursorCapable ? { cursor_mode: true } : {}),
  };
}

const taskPreviewRowIdentity = (rowType, row) => {
  if (rowType === "voiceCalls") {
    return row?.call_id || row?.id || row?.trace_id || null;
  }
  if (rowType === "sessions") {
    return row?.session_id || row?.id || null;
  }
  if (rowType === "traces") {
    return row?.trace_id || row?.id || null;
  }
  const id = row?.span_id || row?.id;
  return id ? `${row?.trace_id || ""}:${id}:${row?.start_time || ""}` : null;
};

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
  { control, projectId, onTestStateChange, waitForProjectKind = false },
  ref,
) {
  const [currentRowIndex, setCurrentRowIndex] = useState(0);
  const [tableSearch, setTableSearch] = useState("");
  const [expandedCols, setExpandedCols] = useState({});
  // Per-eval test results keyed by eval index:
  //   { [idx]: { status: "running" | "success" | "error", result?, error? } }
  const [testResults, setTestResults] = useState({});
  const [isTesting, setIsTesting] = useState(false);
  const pendingNextRowIndexRef = useRef(null);
  const failedListContinuationRef = useRef(null);
  const queryClient = useQueryClient();

  const formFilters = useWatch({ control, name: "filters" });
  const startDate = useWatch({ control, name: "startDate" });
  const endDate = useWatch({ control, name: "endDate" });
  const evalsDetails = useWatch({ control, name: "evalsDetails" });
  const rowType = useWatch({ control, name: "rowType" }) || "spans";
  const isCursorPreview = isTaskPreviewCursorRowType(rowType);
  // The create form starts with `spans`, then resolves simulator projects to
  // `voiceCalls`. Reuse TaskConfigPanel's cached project-detail query and do
  // not start a list request until that reconciliation is complete. This
  // removes the voice -> span -> voice abort chain from Live Preview without
  // adding another HTTP request (React Query deduplicates the shared key).
  const {
    data: previewProjectDetails,
    isSuccess: previewProjectDetailsResolved,
    isError: previewProjectDetailsError,
    isFetching: previewProjectDetailsFetching,
    refetch: refetchPreviewProjectDetails,
  } = useGetProjectDetails(projectId, waitForProjectKind && Boolean(projectId));
  const previewProjectKindReady = isTaskPreviewProjectKindReady({
    waitForProjectKind,
    projectDetailsResolved: previewProjectDetailsResolved,
    projectSource: previewProjectDetails?.source,
    rowType,
  });

  const apiFilters = useMemo(
    () => buildApiFilterArray(formFilters, startDate, endDate),
    [formFilters, startDate, endDate],
  );
  const previewScopeKey = useMemo(
    () => JSON.stringify([rowType, projectId || null, apiFilters]),
    [apiFilters, projectId, rowType],
  );
  const [listContinuation, setListContinuation] = useState(null);
  const activeListContinuation =
    listContinuation?.scopeKey === previewScopeKey ? listContinuation : null;
  const resumeCursor = activeListContinuation?.cursor || null;
  const previousPreviewScopeKeyRef = useRef(previewScopeKey);
  // Signed cursors are snapshot- and scope-bound. Remove every cached list
  // response for the scope being left so A -> B -> A starts a fresh read
  // instead of resurrecting A's old cursor or accumulated rows.
  useEffect(() => {
    const previousScopeKey = previousPreviewScopeKeyRef.current;
    if (previousScopeKey !== previewScopeKey) {
      queryClient.removeQueries({
        predicate: (query) => {
          const key = query?.queryKey || [];
          if (key[0] !== "task-preview-list") return false;
          return (
            JSON.stringify([key[1], key[2] || null, key[3] || []]) ===
            previousScopeKey
          );
        },
      });
      previousPreviewScopeKeyRef.current = previewScopeKey;
      setListContinuation(null);
      failedListContinuationRef.current = null;
    }
    pendingNextRowIndexRef.current = null;
    setCurrentRowIndex(0);
  }, [previewScopeKey, queryClient]);

  // ── Fetch list of matching rows ──
  const {
    data: listData,
    isLoading: listLoading,
    isFetching: listFetching,
    isError: listError,
    error: listQueryError,
    refetch: refetchList,
  } = useQuery({
    queryKey: [
      "task-preview-list",
      rowType,
      projectId,
      apiFilters,
      resumeCursor,
    ],
    queryFn: async ({ signal }) => {
      if (!projectId) return { rows: [], total: 0, columns: [] };

      // A lazy continuation can traverse several empty bounded chunks before
      // its transport fails. Retry from the last unconsumed signed checkpoint
      // retained by that attempt instead of replaying the already-proven
      // prefix from the cursor stored in React state.
      const failedListContinuation =
        failedListContinuationRef.current?.scopeKey === previewScopeKey
          ? failedListContinuationRef.current
          : null;
      const attemptListContinuation =
        failedListContinuation || activeListContinuation;
      const attemptCursor = attemptListContinuation?.cursor || null;

      // Cursors are opaque and query-bound. Keep every cursor already
      // requested for this exact project/filter scope so a repeated or cyclic
      // backend chain fails closed instead of spinning forever. The pending
      // continuation itself is deliberately not in this set until this
      // request consumes it.
      const requestedCursorIdentities = new Set(
        attemptListContinuation?.requestedCursorIdentities || [],
      );
      const cursorIdentityByToken = new Map();
      if (attemptCursor) {
        const attemptCursorIdentity =
          attemptListContinuation?.cursorIdentity ||
          listCursorBoundaryIdentity({ next_cursor: attemptCursor });
        rememberBoundedListCursorIdentity(
          requestedCursorIdentities,
          attemptCursorIdentity,
        );
        cursorIdentityByToken.set(attemptCursor, attemptCursorIdentity);
      }

      const recordContinuation = (metadata) => {
        const nextCursor = metadata?.next_cursor;
        const nextCursorIdentity = listCursorBoundaryIdentity(metadata);
        if (typeof nextCursor !== "string" || nextCursor.length === 0) {
          throw createListCursorProtocolError(
            "List API returned a repeated continuation cursor",
          );
        }
        rememberBoundedListCursorIdentity(
          requestedCursorIdentities,
          nextCursorIdentity,
        );
        cursorIdentityByToken.set(nextCursor, nextCursorIdentity);
      };

      const continuationResult = (
        nextCursor,
        nextCursorIdentity,
        accumulatedRows = [],
        continuationMetadata = {},
      ) => {
        if (!nextCursor) return null;
        // The shared per-attempt follower checks cycles inside one bounded
        // attempt. This second guard covers a cycle that lands exactly on the
        // attempt boundary and points back to any cursor consumed earlier.
        if (
          typeof nextCursorIdentity !== "string" ||
          requestedCursorIdentities.has(nextCursorIdentity)
        ) {
          throw createListCursorProtocolError(
            "List API returned a repeated continuation cursor",
          );
        }
        return {
          cursor: nextCursor,
          cursorIdentity: nextCursorIdentity,
          requestedCursorIdentities: [...requestedCursorIdentities],
          rows: accumulatedRows,
          ...continuationMetadata,
        };
      };

      const requestList = async (
        url,
        params,
        { voice = false, parser, signal: requestSignal = signal } = {},
      ) => {
        try {
          const response = await requestListWithLegacyCursorFallback({
            request: (nextParams) =>
              axios.get(url, { params: nextParams, signal: requestSignal }),
            params,
            pageParam: voice ? "page" : "page_number",
            firstPage: voice ? 1 : 0,
          });
          return parseAxiosResult(response, parser);
        } catch (error) {
          const failedCursor = params?.cursor;
          if (
            !signal.aborted &&
            typeof failedCursor === "string" &&
            failedCursor.length > 0 &&
            !isListCursorProtocolError(error)
          ) {
            const failedCursorIdentity =
              cursorIdentityByToken.get(failedCursor) ||
              listCursorBoundaryIdentity({ next_cursor: failedCursor });
            failedListContinuationRef.current = {
              scopeKey: previewScopeKey,
              ...attemptListContinuation,
              cursor: failedCursor,
              cursorIdentity: failedCursorIdentity,
              requestedCursorIdentities: [...requestedCursorIdentities].filter(
                (identity) => identity !== failedCursorIdentity,
              ),
              rows: attemptListContinuation?.rows || [],
            };
          }
          throw error;
        }
      };

      const completeListAttempt = (result) => {
        if (failedListContinuationRef.current?.scopeKey === previewScopeKey) {
          failedListContinuationRef.current = null;
        }
        return result;
      };

      if (rowType === "voiceCalls") {
        const requestParams = buildTaskPreviewListParams({
          rowType,
          projectId,
          apiFilters,
        });
        const resp = await requestList(
          endpoints.project.getCallLogs,
          attemptCursor
            ? listContinuationParams(requestParams, attemptCursor)
            : requestParams,
          { voice: true, parser: parseVoiceCallListResponse },
        );
        const exactRows = await collectExactListRows({
          initialResponse: resp,
          initialRows: attemptListContinuation?.rows || [],
          targetRowCount:
            (attemptListContinuation?.rows?.length || 0) +
            requestParams.page_size,
          rowsFromResponse: (response) => response.data.results,
          metadataFromResponse: (response) => response.data,
          cancellationSignal: signal,
          nextResponse: (cursor, requestSignal) =>
            requestList(
              endpoints.project.getCallLogs,
              listContinuationParams(requestParams, cursor),
              {
                voice: true,
                parser: parseVoiceCallListResponse,
                signal: requestSignal,
              },
            ),
          onContinuation: recordContinuation,
          isCurrent: () => !signal.aborted,
          rowIdentity: (row) => taskPreviewRowIdentity(rowType, row),
        });
        const result = exactRows.response.data;
        const rowsOut = exactRows.rows;
        // Bounded cursor chunks may expose only a page-local/lower-bound
        // count. Never let lazy navigation shrink a total already shown for
        // this immutable preview scope, and retain the conservative qualifier.
        const total = Math.max(
          result.count,
          rowsOut.length,
          attemptListContinuation?.total || 0,
        );
        const totalIsLowerBound = Boolean(
          result.count_is_lower_bound ||
            attemptListContinuation?.totalIsLowerBound,
        );
        return completeListAttempt({
          rows: rowsOut,
          total,
          totalIsLowerBound,
          columns: result.config,
          continuation: continuationResult(
            exactRows.nextCursor,
            exactRows.nextCursorIdentity,
            rowsOut,
            {
              total,
              totalIsLowerBound,
            },
          ),
        });
      }

      let url;
      let responseParser;
      switch (rowType) {
        case "traces":
          url = endpoints.project.getTracesForObserveProject();
          responseParser = parseTraceObserveListResponse;
          break;
        case "spans":
          url = endpoints.project.getSpansForObserveProject();
          responseParser = parseSpanObserveListResponse;
          break;
        case "sessions":
          url = endpoints.project.projectSessionList();
          responseParser = parseSessionObserveListResponse;
          break;
        default:
          url = endpoints.project.getSpansForObserveProject();
          responseParser = parseSpanObserveListResponse;
      }

      const requestParams = buildTaskPreviewListParams({
        rowType,
        projectId,
        apiFilters,
      });
      const resp = await requestList(
        url,
        attemptCursor
          ? listContinuationParams(requestParams, attemptCursor)
          : requestParams,
        { parser: responseParser },
      );
      const exactRows = await collectExactListRows({
        initialResponse: resp,
        initialRows: attemptListContinuation?.rows || [],
        targetRowCount:
          (attemptListContinuation?.rows?.length || 0) +
          requestParams.page_size,
        rowsFromResponse: (response) => response.data.table,
        metadataFromResponse: (response) => response.data.metadata,
        cancellationSignal: signal,
        nextResponse: (cursor, requestSignal) =>
          requestList(url, listContinuationParams(requestParams, cursor), {
            parser: responseParser,
            signal: requestSignal,
          }),
        onContinuation: recordContinuation,
        isCurrent: () => !signal.aborted,
        rowIdentity: (row) => taskPreviewRowIdentity(rowType, row),
      });
      const result = exactRows.response.data;
      const rowsOut = exactRows.rows;
      // A bounded continuation may report only its page-local count. Preserve
      // the best total already observed for this immutable preview scope.
      const total = Math.max(
        result.metadata.total_rows || 0,
        rowsOut.length,
        attemptListContinuation?.total || 0,
      );
      const totalIsLowerBound = Boolean(
        result.metadata.total_rows_is_lower_bound ||
          attemptListContinuation?.totalIsLowerBound,
      );
      return completeListAttempt({
        rows: rowsOut,
        total,
        totalIsLowerBound,
        columns: result.config,
        continuation: continuationResult(
          exactRows.nextCursor,
          exactRows.nextCursorIdentity,
          rowsOut,
          {
            total,
            totalIsLowerBound,
          },
        ),
      });
    },
    enabled: !!projectId && previewProjectKindReady,
    refetchOnWindowFocus: false,
    staleTime: 10000,
    // Each continuation result contains the current accumulated preview rows.
    // Drop the superseded cursor-keyed query as soon as it becomes inactive so
    // N browsed rows retain one O(N) result rather than N cumulative snapshots.
    gcTime: 0,
    // A continuation is the same immutable preview scope. Keep its current row
    // visible while the next exact match is resolved.
    placeholderData:
      isCursorPreview && resumeCursor
        ? (previousData) => previousData
        : undefined,
    // Live Preview renders its own generic failure state; suppress backend
    // query text (including ClickHouse exception details) globally.
    meta: { errorHandled: true },
  });

  const retryableListContinuationError = Boolean(
    listError &&
      activeListContinuation?.cursor &&
      !isListCursorProtocolError(listQueryError),
  );
  const retryableColdListError = Boolean(
    listError &&
      !activeListContinuation?.cursor &&
      !isListCursorProtocolError(listQueryError),
  );
  const rows =
    listData?.rows ||
    (retryableListContinuationError ? activeListContinuation?.rows : []) ||
    [];
  const columns = listData?.columns || [];
  const pendingListContinuation = listData?.continuation || null;
  const matchingTotal = listData?.total ?? rows.length;
  const matchingTotalIsLowerBound = listData?.totalIsLowerBound === true;
  const currentRow = rows[currentRowIndex] || null;

  const handleNextRow = useCallback(() => {
    if (currentRowIndex < rows.length - 1) {
      setCurrentRowIndex((index) => index + 1);
      return;
    }
    if (!isCursorPreview || !pendingListContinuation || listFetching) return;

    pendingNextRowIndexRef.current = rows.length;
    setListContinuation({
      scopeKey: previewScopeKey,
      ...pendingListContinuation,
    });
  }, [
    currentRowIndex,
    isCursorPreview,
    listFetching,
    pendingListContinuation,
    previewScopeKey,
    rows.length,
  ]);

  useEffect(() => {
    const nextIndex = pendingNextRowIndexRef.current;
    if (nextIndex === null) return;
    if (rows.length > nextIndex) {
      pendingNextRowIndexRef.current = null;
      setCurrentRowIndex(nextIndex);
      return;
    }
    if (listError || (!listFetching && !pendingListContinuation)) {
      pendingNextRowIndexRef.current = null;
    }
  }, [listError, listFetching, pendingListContinuation, rows.length]);

  // ── Fetch full detail for the currently selected row ──
  const {
    data: spanDetail,
    isLoading: detailLoading,
    isError: detailError,
  } = useQuery({
    queryKey: [
      "task-preview-detail",
      projectId,
      rowType,
      currentRow?.trace_id,
      currentRow?.span_id,
      currentRow?.session_id,
    ],
    queryFn: async ({ signal }) => {
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
            { params: { trace_id: traceId }, signal },
          );
          const voiceResult = parseVoiceCallDetailResponse(data);
          detailData = { ...currentRow, ...voiceResult };
        } catch {
          detailData = { ...currentRow };
        }
      } else if ((rowType === "spans" || rowType === "traces") && traceId) {
        const { data } = await axios.get(endpoints.project.getTrace(traceId), {
          signal,
        });
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
            { params: { page_number: 0, page_size: 30 }, signal },
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
                { signal },
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
    meta: { errorHandled: true },
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
          {(listFetching || previewProjectDetailsFetching) && (
            <CircularProgress size={12} />
          )}
        </Box>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "11px", display: "block" }}
        >
          {projectId
            ? "Browse a row matching your current filters"
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
        ) : waitForProjectKind && previewProjectDetailsError ? (
          <Box
            role="status"
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 1,
              minHeight: 160,
              justifyContent: "center",
              textAlign: "center",
            }}
          >
            <Typography variant="body2" color="error" sx={{ fontSize: "12px" }}>
              {QUERY_FAILED_RETRY_MESSAGE}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              disabled={previewProjectDetailsFetching}
              onClick={() => refetchPreviewProjectDetails()}
            >
              Retry search
            </Button>
          </Box>
        ) : !previewProjectKindReady || listLoading ? (
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
        ) : retryableListContinuationError ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 1,
              minHeight: 160,
              textAlign: "center",
            }}
          >
            <Typography variant="body2" sx={{ fontSize: "12px" }}>
              The exact preview was paused.
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px" }}
            >
              Your saved position is retained. Retry to continue from it.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              disabled={listFetching}
              onClick={() => refetchList()}
            >
              Retry search
            </Button>
          </Box>
        ) : listError ? (
          <Box
            role="status"
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 1,
              minHeight: 160,
              justifyContent: "center",
              textAlign: "center",
            }}
          >
            <Typography variant="body2" color="error" sx={{ fontSize: "12px" }}>
              {QUERY_FAILED_RETRY_MESSAGE}
            </Typography>
            {retryableColdListError && (
              <Button
                size="small"
                variant="outlined"
                disabled={listFetching}
                onClick={() => refetchList()}
              >
                Retry search
              </Button>
            )}
          </Box>
        ) : pendingListContinuation && rows.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 1,
              minHeight: 160,
              textAlign: "center",
            }}
          >
            <Typography variant="body2" sx={{ fontSize: "12px" }}>
              Preparing the exact preview.
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px" }}
            >
              Continue from the saved position to load the next bounded batch.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              disabled={listFetching}
              onClick={() =>
                setListContinuation({
                  scopeKey: previewScopeKey,
                  ...pendingListContinuation,
                })
              }
            >
              Continue search
            </Button>
          </Box>
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
                {(matchingTotalIsLowerBound || matchingTotal > rows.length) && (
                  <Typography
                    component="span"
                    sx={{
                      fontSize: "11px",
                      color: "text.disabled",
                      ml: 0.5,
                    }}
                  >
                    ({matchingTotalIsLowerBound ? "≥" : ""}
                    {matchingTotal} matching total)
                  </Typography>
                )}
              </Typography>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <IconButton
                  aria-label="Previous row"
                  size="small"
                  disabled={listFetching || currentRowIndex === 0}
                  onClick={() => setCurrentRowIndex((i) => Math.max(0, i - 1))}
                  sx={{ width: 24, height: 24 }}
                >
                  <Iconify icon="mdi:chevron-left" width={16} />
                </IconButton>
                <IconButton
                  aria-label="Next row"
                  size="small"
                  disabled={
                    listFetching ||
                    (currentRowIndex >= rows.length - 1 &&
                      (!isCursorPreview || !pendingListContinuation))
                  }
                  onClick={handleNextRow}
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
            ) : detailError ? (
              <Typography
                variant="body2"
                color="error"
                sx={{ fontSize: "12px", textAlign: "center", py: 3 }}
              >
                {QUERY_FAILED_RETRY_MESSAGE}
              </Typography>
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
  waitForProjectKind: PropTypes.bool,
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
                    const label = mappingPathLabel(field);
                    const isPath = isMappingPath(field);
                    // Tri-state: `missing` warns, `unknown` (session traces
                    // whose spans weren't fetched) stays silent — the BE is
                    // the authoritative resolver at test time.
                    const hit =
                      spanDetail && isPath
                        ? resolvePath(spanDetail, field)
                        : { status: "unknown" };
                    const showNotInRow = hit.status === "missing" && isPath;
                    const showWarn = showNotInRow || (!isPath && !!label);
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
                          title={label}
                          show={!!label}
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
                            {label || "—"}
                          </Typography>
                        </CustomTooltip>
                        {showNotInRow && (
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
