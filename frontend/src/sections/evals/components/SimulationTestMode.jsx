/* eslint-disable react/prop-types */
import {
  Autocomplete,
  Box,
  CircularProgress,
  IconButton,
  InputAdornment,
  MenuItem,
  Select,
  Skeleton,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { canonicalEntries, canonicalKeys } from "src/utils/utils";
import CustomAudioPlayer from "src/components/custom-audio/CustomAudioPlayer";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import DraggableColResizer from "src/components/draggable-col-resizer";
import { JsonValueTree } from "./DatasetTestMode";
import { buildCompositeRuntimeConfig } from "../Helpers/compositeRuntimeConfig";
import EvalResultDisplay from "./EvalResultDisplay";
import useErrorLocalizerPoll from "../hooks/useErrorLocalizerPoll";
import {
  useExecuteCompositeEval,
  useExecuteCompositeEvalAdhoc,
} from "../hooks/useCompositeEval";

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

// Priority for the Columns/Value display table. Dot-hierarchy keys
// (call.transcript, agent.name, etc.) are matched by prefix so the
// ordering survives the flatten-to-leaves step below.
const PRIORITY_PREFIXES = [
  "call.transcript",
  "call.summary",
  "call.user_chat_transcript",
  "call.assistant_chat_transcript",
  "call.voice_recording",
  "call.stereo_recording",
  "call.assistant_recording",
  "call.customer_recording",
  "call.recording_url",
  "call.stereo_recording_url",
  "call.agent_prompt",
  "call.", // remaining call-level leaves
  "eval_", // resolved eval results (still flat)
  "scenario.columns.",
  "scenario.info.",
  "scenario.",
  "simulation.",
  "agent.",
  "persona.",
  "prompt.",
  "tool_outputs",
];

// Walk a nested object to its leaves, emitting [dotPath, value] pairs.
// Used for both the display table and fieldNames so the mapping
// dropdown and the Columns/Value table share the same leaf set.
function flattenLeaves(obj, prefix) {
  const result = [];
  for (const [k, v] of canonicalEntries(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (
      v &&
      typeof v === "object" &&
      !Array.isArray(v) &&
      canonicalKeys(v).length > 0 &&
      canonicalKeys(v).length < 50
    ) {
      result.push(...flattenLeaves(v, path));
    } else {
      result.push([path, v]);
    }
  }
  return result;
}
// Dot-hierarchy runtime vocabulary. These paths mirror the backend dot
// aliases in xl.py — voice sims expose `call.voice_recording` etc.,
// text sims expose `call.user_chat_transcript` etc. Keys here must stay
// in sync with TRANSCRIPT_DOT_ALIASES on the backend.
const VOICE_RESOLVER_KEYS = [
  "call.voice_recording",
  "call.stereo_recording",
  "call.assistant_recording",
  "call.customer_recording",
];
const TEXT_RESOLVER_KEYS = [
  "call.user_chat_transcript",
  "call.assistant_chat_transcript",
];
const COMMON_RESOLVER_KEYS = ["call.transcript", "call.agent_prompt"];

// Parse the backend's concatenated `transcript` string (format:
// "user: msg\nassistant: msg") back into user/assistant-only transcripts
// so we can preview the chat-specific vocabulary on the frontend. The
// backend rebuilds these from ChatMessageModel at eval run time — this
// is only for the test panel preview.
function splitChatTranscript(transcript) {
  if (!transcript || typeof transcript !== "string")
    return { user: "", assistant: "" };
  const lines = transcript.split("\n");
  const userLines = [];
  const assistantLines = [];
  for (const line of lines) {
    const match = /^(user|assistant)\s*:\s*(.*)$/i.exec(line);
    if (!match) continue;
    if (match[1].toLowerCase() === "user") userLines.push(match[2]);
    else assistantLines.push(match[2]);
  }
  return { user: userLines.join("\n"), assistant: assistantLines.join("\n") };
}

function sortEntries(entries) {
  const getOrder = (key) => {
    for (let i = 0; i < PRIORITY_PREFIXES.length; i++) {
      const p = PRIORITY_PREFIXES[i];
      if (key === p || key.startsWith(p)) return i;
    }
    return PRIORITY_PREFIXES.length;
  };
  return [...entries].sort(([a], [b]) => getOrder(a) - getOrder(b));
}

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

const SimulationTestMode = React.forwardRef(
  (
    {
      templateId,
      model = "turing_large",
      variables = [],
      codeParams = {},
      onTestResult,
      onColumnsLoaded,
      onClearResult,
      onReadyChange,
      errorLocalizerEnabled = false,
      initialMapping = null,
      initialRunTestId = "",
      isComposite = false,
      compositeAdhocConfig = null,
    },
    ref,
  ) => {
    // Run tests (simulations) — paginated via infinite scroll in the
    // Autocomplete listbox. The dropdown fetches page 1 on mount and
    // appends subsequent pages when the user scrolls near the bottom.
    const [runTests, setRunTests] = useState([]);
    const [loadingRunTests, setLoadingRunTests] = useState(false);
    const [loadingMoreRunTests, setLoadingMoreRunTests] = useState(false);
    const [runTestsPage, setRunTestsPage] = useState(1);
    const [runTestsHasMore, setRunTestsHasMore] = useState(true);
    const [selectedRunTestId, setSelectedRunTestId] =
      useState(initialRunTestId || "");

    // Run test context (agent def, scenarios, persona, evals)
    const [runTestContext, setRunTestContext] = useState(null);

    // Test executions (runs within a simulation)
    const [executions, setExecutions] = useState([]);
    const [selectedExecutionId, setSelectedExecutionId] = useState("");

    // Call executions (individual calls)
    const [calls, setCalls] = useState([]);
    const [totalCalls, setTotalCalls] = useState(0);
    // Derive "is the current selection stale w.r.t. the last fetch" at
    // render time. React effects run *after* paint, so tracking a
    // `hasFetchedCalls` boolean still left a render-frame gap where the
    // empty state flashed and the spinner appeared late. Comparing the
    // run-test id against the last-fetched key makes this visible in
    // the same render that the prop changed.
    const [lastFetchedCallsKey, setLastFetchedCallsKey] = useState(null);
    const [currentCallIndex, setCurrentCallIndex] = useState(0);
    const [loadingCalls, setLoadingCalls] = useState(false);
    // Synchronous: true between the prop change and the fetch
    // completing. Hides the empty-state and keeps the spinner on screen
    // across the render-frame gap.
    const isPendingCallsFetch =
      !!selectedExecutionId && lastFetchedCallsKey !== selectedExecutionId;

    // Call detail
    const [callDetail, setCallDetail] = useState(null);
    const [loadingDetail, setLoadingDetail] = useState(false);

    // Per-call cache so toggling calls doesn't refetch or re-walk the same
    // payload. Keyed by call id. Each entry is `{ detail, fieldNames? }`
    // — fieldNames fills on first walk and is reused on repeat toggles.
    const detailCacheRef = useRef(new Map());

    // Columns/Value table — user-resizable key column. Drag the divider
    // between key and value to widen long dotted paths.
    const [keyColWidth, setKeyColWidth] = useState(200);
    const keyColWidthRef = useRef(200);
    useEffect(() => {
      keyColWidthRef.current = keyColWidth;
    }, [keyColWidth]);

    // Template ID ref
    const templateIdRef = useRef(templateId);
    useEffect(() => {
      templateIdRef.current = templateId;
    }, [templateId]);

    // Display
    const [tableSearch, setTableSearch] = useState("");
    const [expandedCols, setExpandedCols] = useState({});

    // Variable mapping (stores display keys internally; scenario fields are
    // translated to their UUID via scenarioKeyMap when emitted to the parent).
    // Seeded from `initialMapping` when editing an existing eval (TH-4302).
    const [mapping, setMapping] = useState(
      initialMapping && typeof initialMapping === "object"
        ? { ...initialMapping }
        : {},
    );
    // displayKey ("scenario_<col_name>") -> scenario column UUID. The backend
    // resolver at run time only accepts scenario column UUIDs, not names, so
    // we persist the UUID while the dropdown still shows the friendly label.
    const scenarioKeyMap = useRef({});

    // Eval result
    const [, setIsRunning] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    // Async error localization poll — see DatasetTestMode for rationale.
    const { state: errorLocalizerState, start: startErrorLocalizerPoll } =
      useErrorLocalizerPoll();
    const executeComposite = useExecuteCompositeEval();
    const executeCompositeAdhoc = useExecuteCompositeEvalAdhoc();

    // 1. Fetch run tests (simulations) — infinite-scroll pagination.
    // Page 1 loads on mount; subsequent pages fetch via onScroll on the
    // Autocomplete listbox. We use a large-ish page_size (50) to reduce
    // round-trips while keeping the initial payload small.
    const RUN_TESTS_PAGE_SIZE = 50;
    const fetchRunTestsPage = useCallback(async (pageNum) => {
      const isFirst = pageNum === 1;
      if (isFirst) setLoadingRunTests(true);
      else setLoadingMoreRunTests(true);
      try {
        const { data } = await axios.get(endpoints.runTests.list, {
          // Backend's ExtendedPageNumberPagination uses `limit`, not
          // `page_size`, as the page-size query param. Sending
          // `page_size` silently falls back to the 10-item default and
          // the dropdown maxed out at 10 rows regardless of scroll.
          params: { page: pageNum, limit: RUN_TESTS_PAGE_SIZE },
        });
        const items = Array.isArray(data?.results) ? data.results : [];
        const total = data?.count ?? 0;
        setRunTests((prev) => (isFirst ? items : [...prev, ...items]));
        setRunTestsPage(pageNum);
        setRunTestsHasMore(pageNum * RUN_TESTS_PAGE_SIZE < total);
      } catch {
        if (isFirst) setRunTests([]);
        setRunTestsHasMore(false);
      } finally {
        if (isFirst) setLoadingRunTests(false);
        else setLoadingMoreRunTests(false);
      }
    }, []);

    useEffect(() => {
      fetchRunTestsPage(1);
    }, [fetchRunTestsPage]);

    const handleRunTestsListboxScroll = useCallback(
      (event) => {
        const node = event.currentTarget;
        const nearBottom =
          node.scrollTop + node.clientHeight >= node.scrollHeight - 24;
        if (nearBottom && runTestsHasMore && !loadingMoreRunTests) {
          fetchRunTestsPage(runTestsPage + 1);
        }
      },
      [runTestsHasMore, loadingMoreRunTests, runTestsPage, fetchRunTestsPage],
    );

    // 2. Fetch run test detail (context) + executions when selected
    useEffect(() => {
      if (!selectedRunTestId) {
        setExecutions([]);
        setSelectedExecutionId("");
        setRunTestContext(null);
        return;
      }
      const fetchAll = async () => {
        try {
          // Fetch detail (agent def, scenarios, persona, evals) and executions in parallel
          // Simulate APIs return data directly (no {status, result} wrapper)
          const [detailRes, execRes] = await Promise.all([
            axios
              .get(endpoints.runTests.detail(selectedRunTestId))
              .catch(() => ({ data: {} })),
            axios.get(endpoints.runTests.detailExecutions(selectedRunTestId), {
              params: { page: 1, limit: 100 },
            }),
          ]);
          // Detail: flat serializer data
          setRunTestContext(detailRes.data || null);
          // Executions: paginated {results: [...]}
          const items = execRes.data?.results || [];
          setExecutions(items);
          if (items.length > 0) {
            setSelectedExecutionId(items[0].id || "");
          }
        } catch {
          setExecutions([]);
          setRunTestContext(null);
        }
      };
      fetchAll();
    }, [selectedRunTestId]);

    // 3. Fetch call executions for the selected execution
    useEffect(() => {
      if (!selectedExecutionId) {
        setCalls([]);
        setTotalCalls(0);
        setCurrentCallIndex(0);
        setCallDetail(null);
        setLastFetchedCallsKey(null);
        return;
      }
      // Flip loading synchronously so the spinner shows as soon as the
      // user picks a run. Empty-state visibility comes from the
      // render-time `isPendingCallsFetch` comparison.
      setLoadingCalls(true);
      const fetchCalls = async () => {
        try {
          const { data } = await axios.get(
            endpoints.testExecutions.list(selectedExecutionId),
            { params: { page: 1, limit: 50 } },
          );
          const items = data?.results || [];
          const total = data?.count || items.length;
          setCalls(items);
          setTotalCalls(total);
          setCurrentCallIndex(0);
        } catch {
          setCalls([]);
          setTotalCalls(0);
        } finally {
          setLoadingCalls(false);
          setLastFetchedCallsKey(selectedExecutionId);
        }
      };
      fetchCalls();
    }, [selectedExecutionId]);

    // Current call
    const currentCall = calls[currentCallIndex] || null;

    // 4. Fetch call detail, resolve all IDs, flatten into one table
    useEffect(() => {
      if (!currentCall) {
        setCallDetail(null);
        return;
      }

      const cacheKey = currentCall.id || "";
      const cached = cacheKey && detailCacheRef.current.get(cacheKey);
      if (cached) {
        setCallDetail(cached.detail);
        setLoadingDetail(false);
        return;
      }

      const fetchDetail = async () => {
        setLoadingDetail(true);
        try {
          const callId = currentCall.id;
          let callData = currentCall;
          if (callId) {
            const { data } = await axios.get(
              endpoints.runTests.callExecutionDetail(callId),
            );
            callData = data || currentCall;
          }

          // Keys to skip from the raw callData pass-through. Entries
          // that now live under nested groups (`call.*`, `agent.*`,
          // `persona.*`, etc.) are listed here so they don't duplicate
          // between the raw and nested representations.
          const SKIP = new Set([
            "id",
            "scenario_id",
            "agent_definition_used_id",
            "simulator_agent_id",
            "service_provider_call_id",
            "session_id",
            "original_call_execution_id",
            "customer_call_id",
            "eval_outputs",
            "scenario_columns",
            "eval_metrics",
            "is_snapshot",
            "snapshot_timestamp",
            "rerun_type",
            "rerun_snapshots",
            "processing_skipped",
            "processing_skip_reason",
            "timestamp",
            // Nested-group duplicates — now under `flat.call.*`
            "transcript",
            "audio_url",
            "recordings",
            "recording_url",
            "stereo_recording_url",
            "status",
            "ended_reason",
            "call_summary",
            "duration",
            "duration_seconds",
            "overall_score",
            "phone_number",
            "simulation_call_type",
            "call_type",
            "start_time",
            "customer_name",
            "provider_call_data",
          ]);

          // Nested structure — keys use dot-hierarchy for clarity and
          // to group related fields in the mapping dropdown. Contract
          // with the backend dot aliases in
          // simulate/temporal/activities/xl.py (CONTEXT_MAP_DOT_ALIASES
          // + TRANSCRIPT_DOT_ALIASES). When adding a new key, add it
          // in both places.
          // `prompt` bucket is added lazily below only when a prompt
          // template is actually attached to the run. Seeding it empty
          // leaked `prompt` as a selectable key into the mapping dropdown.
          const flat = {
            simulation: {},
            agent: {},
            persona: {},
            scenario: { info: {}, columns: {} },
            call: {},
          };

          // -- Simulation & agent context (from run test detail) --
          if (runTestContext) {
            if (runTestContext.name) flat.simulation.name = runTestContext.name;
            if (runTestContext.source_type_display)
              flat.simulation.type = runTestContext.source_type_display;

            const ad = runTestContext.agent_definition_detail;
            if (ad) {
              if (ad.agent_name) flat.agent.name = ad.agent_name;
              if (ad.agent_type) flat.agent.type = ad.agent_type;
              if (ad.provider) flat.agent.provider = ad.provider;
              if (ad.contact_number)
                flat.agent.contact_number = ad.contact_number;
              if (ad.model) flat.agent.model = ad.model;
              if (ad.language) flat.agent.language = ad.language;
              if (ad.description) flat.agent.description = ad.description;
            }

            const persona = runTestContext.simulator_agent_detail;
            if (persona) {
              if (persona.name) flat.persona.name = persona.name;
              if (persona.prompt) flat.persona.prompt = persona.prompt;
              if (persona.description)
                flat.persona.description = persona.description;
              if (persona.voice_name)
                flat.persona.voice_name = persona.voice_name;
              if (persona.model) flat.persona.model = persona.model;
              if (persona.initial_message)
                flat.persona.initial_message = persona.initial_message;
            }

            const promptTemplate = runTestContext.prompt_template_detail;
            if (promptTemplate) {
              flat.prompt = {};
              if (promptTemplate.name) flat.prompt.name = promptTemplate.name;
              if (promptTemplate.description)
                flat.prompt.description = promptTemplate.description;
            }
          }

          // -- Scenario columns: display key `scenario.columns.<name>`,
          // persisted mapping value is the column UUID (backend resolver
          // accepts UUIDs, not names).
          const sc = callData.scenario_columns;
          scenarioKeyMap.current = {};
          if (sc && typeof sc === "object") {
            for (const [uuid, col] of Object.entries(sc)) {
              if (col?.column_name && col?.value !== undefined) {
                flat.scenario.columns[col.column_name] = col.value;
                scenarioKeyMap.current[`scenario.columns.${col.column_name}`] =
                  uuid;
              }
            }
          }

          // -- Scenario row metadata (Scenarios FK) --
          const scenarioId = callData.scenario_id || callData.scenario?.id;
          const scenariosDetail = runTestContext?.scenarios_detail || [];
          const scenarioRow =
            scenariosDetail.find((s) => s.id === scenarioId) ||
            scenariosDetail[0];
          if (scenarioRow) {
            if (scenarioRow.name) flat.scenario.info.name = scenarioRow.name;
            if (scenarioRow.description)
              flat.scenario.info.description = scenarioRow.description;
            if (scenarioRow.scenario_type)
              flat.scenario.info.type = scenarioRow.scenario_type;
            if (scenarioRow.source)
              flat.scenario.info.source = scenarioRow.source;
          }

          // -- Call-level runtime vocabulary — nested under `call.*`. --
          const callType = callData.call_type || callData.simulation_call_type;
          const isTextCall =
            typeof callType === "string" &&
            ["text", "chat", "prompt"].includes(callType.toLowerCase());

          if (isTextCall) {
            const rawTranscript =
              typeof callData.transcript === "string"
                ? callData.transcript
                : "";
            const { user, assistant } = splitChatTranscript(rawTranscript);
            flat.call.transcript = rawTranscript;
            flat.call.user_chat_transcript = user;
            flat.call.assistant_chat_transcript = assistant;
          } else {
            // Voice sim: pull recordings from the serializer's
            // `recordings` dict / `audio_url`, with provider_call_data
            // fallback for shapes that don't normalize cleanly.
            const rec = callData.recordings || {};
            flat.call.transcript =
              typeof callData.transcript === "string"
                ? callData.transcript
                : "";
            flat.call.voice_recording =
              callData.audio_url ||
              rec.combined ||
              rec.recording_url ||
              rec.mono ||
              "";
            flat.call.stereo_recording =
              rec.stereo ||
              rec.stereo_recording_url ||
              callData.stereo_recording_url ||
              "";
            flat.call.assistant_recording =
              rec.assistant || rec.assistant_recording || "";
            flat.call.customer_recording =
              rec.customer || rec.customer_recording || "";

            if (
              !flat.call.voice_recording ||
              !flat.call.stereo_recording ||
              !flat.call.assistant_recording ||
              !flat.call.customer_recording
            ) {
              const pcd = callData.provider_call_data;
              if (pcd && typeof pcd === "object") {
                for (const providerData of Object.values(pcd)) {
                  const pRec = providerData?.recording;
                  if (pRec && typeof pRec === "object") {
                    if (!flat.call.voice_recording && pRec.combined)
                      flat.call.voice_recording = pRec.combined;
                    if (!flat.call.stereo_recording && pRec.stereo)
                      flat.call.stereo_recording = pRec.stereo;
                    if (!flat.call.assistant_recording && pRec.assistant)
                      flat.call.assistant_recording = pRec.assistant;
                    if (!flat.call.customer_recording && pRec.customer)
                      flat.call.customer_recording = pRec.customer;
                  }
                }
              }
            }
          }

          // agent_prompt — server-side resolves to the agent_version
          // snapshot description. Best-effort preview here.
          const adSnapshot =
            runTestContext?.agent_version?.configuration_snapshot;
          flat.call.agent_prompt =
            adSnapshot?.description ||
            runTestContext?.prompt_template_detail?.description ||
            "";

          // Call-level primitive metadata. CallExecutionDetailSerializer
          // renames `recording_url` → `audio_url` and exposes a
          // `recordings` dict; there's no raw `recording_url` field in
          // the response, so we read `audio_url` / `recordings` instead.
          if (callData.call_summary) flat.call.summary = callData.call_summary;
          if (callData.ended_reason)
            flat.call.ended_reason = callData.ended_reason;
          if (callData.duration_seconds != null)
            flat.call.duration_seconds = callData.duration_seconds;
          if (callData.duration != null && flat.call.duration_seconds == null)
            flat.call.duration_seconds = callData.duration;
          if (callData.status) flat.call.status = callData.status;
          if (callData.phone_number)
            flat.call.phone_number = callData.phone_number;
          if (callData.overall_score != null)
            flat.call.overall_score = callData.overall_score;
          if (callData.audio_url) flat.call.recording_url = callData.audio_url;
          const stereoUrl =
            callData.recordings?.stereo ??
            callData.recordings?.stereo_recording_url;
          if (stereoUrl) flat.call.stereo_recording_url = stereoUrl;
          if (callType) flat.simulation.call_type = callType;

          // -- Eval results: resolve UUID keys → {eval_name: score + reason} --
          const em = callData.eval_metrics || {};
          const eo = callData.eval_outputs || {};
          const evalEntries = Object.keys(em).length ? em : eo;
          for (const [, ev] of Object.entries(evalEntries)) {
            const name = ev.name || ev.eval_name || "eval";
            flat[`eval_${name}`] = {
              score: ev.value || ev.score,
              reason: ev.reason || ev.explanation,
            };
          }

          // -- Raw callData pass-through (after SKIP). These are top-
          // level fields that don't belong in a nested group (like
          // timing, tokens, latency metrics) — displayed as-is.
          for (const [k, v] of canonicalEntries(callData)) {
            if (SKIP.has(k)) continue;
            if (k in flat) continue;
            flat[k] = v;
          }

          if (cacheKey) {
            detailCacheRef.current.set(cacheKey, { detail: flat });
          }
          setCallDetail(flat);
        } catch {
          setCallDetail(currentCall);
        } finally {
          setLoadingDetail(false);
        }
      };
      fetchDetail();
    }, [currentCall, runTestContext]);

    // Field names for variable mapping. Expand nested object keys into
    // dot-notation paths, then filter out non-leaf intermediate keys so
    // `agent` / `call` don't appear as pickable options — only
    // leaves like `agent.name` or `call.transcript`.
    //
    // Memoised by `callDetail` reference identity and backed by
    // `detailCacheRef`, so toggling to a previously-viewed call reuses
    // the walked output without re-enumerating the tree.
    const fieldNames = useMemo(() => {
      if (!callDetail) return [];

      // Cache hit: same detail reference was walked on a prior toggle.
      for (const entry of detailCacheRef.current.values()) {
        if (entry.detail === callDetail && entry.fieldNames) {
          return entry.fieldNames;
        }
      }

      const keys = [];
      // Don't recurse into known-heavy Vapi dumps — the key stays
      // selectable but the walker finishes in tens of ms instead of
      // enumerating multi-MB raw_log / metrics_data contents.
      const NO_RECURSE_KEYS = new Set([
        "raw_log",
        "rawLog",
        "metrics_data",
        "metricsData",
        "call_logs",
        "callLogs",
        "provider_transcript",
        "providerTranscript",
        "provider_call_data",
        "providerCallData",
      ]);
      const walk = (obj, prefix) => {
        // canonicalEntries filters out the camelCase aliases the axios
        // interceptor adds alongside snake_case keys.
        const entries = canonicalEntries(obj);
        for (const [k, v] of entries) {
          const path = prefix ? `${prefix}.${k}` : k;
          keys.push(path);
          if (NO_RECURSE_KEYS.has(k)) continue;
          if (
            v &&
            typeof v === "object" &&
            !Array.isArray(v) &&
            canonicalKeys(v).length < 5000
          ) {
            walk(v, path);
          }
        }
      };
      walk(callDetail, "");
      // Leaf-only filter: drop paths that resolve to a non-array object
      // (those are intermediate groups, not bindable values).
      const leaves = keys.filter((k) => {
        const val = k.split(".").reduce((o, p) => o?.[p], callDetail);
        return (
          val === null ||
          val === undefined ||
          typeof val !== "object" ||
          Array.isArray(val)
        );
      });

      // Persist the walked output into the cache so repeat toggles skip
      // the recursion entirely.
      for (const [key, entry] of detailCacheRef.current.entries()) {
        if (entry.detail === callDetail) {
          detailCacheRef.current.set(key, { ...entry, fieldNames: leaves });
          break;
        }
      }

      return leaves;
    }, [callDetail]);

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
      setMapping((prev) => {
        const next = { ...prev };
        let changed = false;
        variables.forEach((v) => {
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

    // Notify parent EvalPickerConfigFull when the mapping is complete so the
    // "Add Evaluation" button can enable. Without this bridge, sourceReady
    // stays false forever and save is permanently disabled for the
    // "simulation" / "test" / "create-simulate" source modes.
    const isReady = useMemo(
      () =>
        !!selectedRunTestId &&
        variables.length > 0 &&
        variables.every((v) => !!mapping[v]),
      [selectedRunTestId, variables, mapping],
    );

    // Translate scenario display keys → UUIDs before handing mapping to
    // the parent. The backend resolver matches on column UUID, so without
    // this, saved evals that reference scenario columns fail at run time
    // with "Column mapping mismatch".
    const persistedMapping = useMemo(() => {
      const out = {};
      for (const [variable, field] of Object.entries(mapping)) {
        out[variable] = scenarioKeyMap.current[field] || field;
      }
      return out;
    }, [mapping, callDetail]);

    useEffect(() => {
      onReadyChange?.(isReady, persistedMapping);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isReady, persistedMapping]);

    // Run test
    const handleRunTest = useCallback(async () => {
      const tid = templateIdRef.current;
      if (!tid) {
        onTestResult?.(false, "No template ID — save the eval first");
        return;
      }
      setIsRunning(true);
      setResult(null);
      setError(null);
      try {
        const evalMapping = {};
        for (const variable of variables) {
          const mappedField = mapping[variable];
          if (mappedField && callDetail) {
            // Resolve dot-notation paths (e.g. "scenario_outcome", "eval_data_privacy.score")
            const val = mappedField.includes(".")
              ? mappedField.split(".").reduce((obj, k) => obj?.[k], callDetail)
              : callDetail[mappedField];
            if (val !== undefined) {
              evalMapping[variable] =
                typeof val === "object"
                  ? JSON.stringify(val)
                  : String(val ?? "");
            }
          }
        }
        // Auto-context: single-eval playground resolves call_id server-side.
        // Composite execution expects the concrete call context directly.
        const _callId = currentCall?.id;
        const compositeConfig = buildCompositeRuntimeConfig({
          codeParams,
        });
        const compositePayload = {
          mapping: evalMapping,
          model,
          error_localizer: errorLocalizerEnabled,
          config: compositeConfig,
          ...(callDetail ? { call_context: callDetail } : {}),
        };

        const { data } = isComposite
          ? compositeAdhocConfig
            ? {
                data: {
                  status: true,
                  result: await executeCompositeAdhoc.mutateAsync({
                    ...compositeAdhocConfig,
                    ...compositePayload,
                  }),
                },
              }
            : {
                data: {
                  status: true,
                  result: await executeComposite.mutateAsync({
                    templateId: tid,
                    payload: compositePayload,
                  }),
                },
              }
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
              ...(_callId ? { call_id: _callId } : {}),
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
      callDetail,
      currentCall,
      onTestResult,
      errorLocalizerEnabled,
      isComposite,
      compositeAdhocConfig,
      startErrorLocalizerPoll,
      codeParams,
      model,
      executeComposite,
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

    // Build label for run test dropdown
    const getRunTestLabel = (rt) => {
      const name = rt?.name || "";
      const agent =
        rt?.agent_definition_name || rt?.agent_definition?.agent_name || "";
      const type = rt?.simulation_type || rt?.simulation_call_type || "";
      return [name, agent, type].filter(Boolean).join(" — ") || rt?.id || "";
    };

    // The skeleton stays visible the entire time the variable mapping is
    // pending — that covers initial run-test list loads, executions not yet
    // resolved in edit mode (initialRunTestId known but selectedExecutionId
    // still empty), the calls fetch, and the call-detail fetch (including
    // when switching calls via the navigator, where `callDetail` still
    // holds stale data until the new fetch resolves). We only hide it once
    // we either have fresh callDetail or confirm an empty execution.
    const isConfirmedEmpty =
      !!selectedExecutionId &&
      totalCalls === 0 &&
      !loadingCalls &&
      !isPendingCallsFetch;
    const isMappingPending =
      !isConfirmedEmpty &&
      (loadingRunTests ||
        loadingCalls ||
        isPendingCallsFetch ||
        loadingDetail ||
        (!callDetail &&
          (!!initialRunTestId ||
            !!selectedRunTestId ||
            !!selectedExecutionId)));

    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {/* Simulation (Run Test) selector */}
        <Box>
          <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
            Simulation<span style={{ color: "#d32f2f" }}>*</span>
          </Typography>
          <Autocomplete
            size="small"
            options={runTests}
            getOptionLabel={getRunTestLabel}
            value={runTests.find((rt) => rt.id === selectedRunTestId) || null}
            onChange={(_, val) => setSelectedRunTestId(val?.id || "")}
            loading={loadingRunTests || loadingMoreRunTests}
            disabled={!!initialRunTestId}
            openOnFocus
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder="Search simulations..."
                InputProps={{
                  ...params.InputProps,
                  sx: { ...params.InputProps.sx, fontSize: "13px" },
                  endAdornment: loadingRunTests ? (
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
              const type =
                option.simulation_type || option.simulation_call_type || "";
              return (
                <Box
                  component="li"
                  key={key}
                  {...rest}
                  sx={{
                    ...rest.sx,
                    fontSize: "13px",
                    display: "flex",
                    gap: 1,
                    alignItems: "center",
                  }}
                >
                  <Iconify
                    icon={
                      type === "voice"
                        ? "mdi:phone-outline"
                        : type === "chat"
                          ? "mdi:chat-outline"
                          : "mdi:play-circle-outline"
                    }
                    width={16}
                    sx={{
                      color:
                        type === "voice"
                          ? "warning.main"
                          : type === "chat"
                            ? "info.main"
                            : "primary.main",
                      flexShrink: 0,
                    }}
                  />
                  <Box sx={{ minWidth: 0 }}>
                    <Typography
                      variant="body2"
                      sx={{ fontSize: "13px" }}
                      noWrap
                    >
                      {option.name || option.id}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.disabled"
                      sx={{ fontSize: "11px" }}
                      noWrap
                    >
                      {[
                        option.agent_definition_name ||
                          option.agent_definition?.agent_name,
                        type,
                      ]
                        .filter(Boolean)
                        .join(" • ")}
                    </Typography>
                  </Box>
                </Box>
              );
            }}
            ListboxProps={{
              style: { maxHeight: 300 },
              onScroll: handleRunTestsListboxScroll,
            }}
            noOptionsText={loadingRunTests ? "Loading..." : "No simulations"}
          />
        </Box>

        {/* Execution selector (if multiple runs exist) */}
        {executions.length > 1 && (
          <Box>
            <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
              Execution Run
            </Typography>
            <Select
              size="small"
              fullWidth
              value={selectedExecutionId}
              onChange={(e) => setSelectedExecutionId(e.target.value)}
              sx={{ fontSize: "13px" }}
            >
              {executions.map((ex, i) => (
                <MenuItem key={ex.id} value={ex.id} sx={{ fontSize: "13px" }}>
                  Run {i + 1} — {ex.status || "completed"}{" "}
                  {ex.created_at
                    ? `(${new Date(ex.created_at).toLocaleDateString()})`
                    : ""}
                </MenuItem>
              ))}
            </Select>
          </Box>
        )}

        {/* Empty state */}
        {selectedExecutionId &&
          !loadingCalls &&
          !isPendingCallsFetch &&
          totalCalls === 0 && (
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
              No calls in this simulation
            </Typography>
            <Typography variant="caption" color="text.disabled">
              Add calls to the simulation before running a test
            </Typography>
          </Box>
        )}

        {/* Call navigator */}
        {selectedExecutionId &&
          totalCalls > 0 &&
          !loadingCalls &&
          !isPendingCallsFetch && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Call {currentCallIndex + 1} of {totalCalls}
            </Typography>
            <IconButton
              size="small"
              disabled={currentCallIndex === 0}
              onClick={() => {
                setCurrentCallIndex((i) => Math.max(0, i - 1));
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
              disabled={currentCallIndex >= totalCalls - 1}
              onClick={() => {
                setCurrentCallIndex((i) => Math.min(totalCalls - 1, i + 1));
                setResult(null);
                setError(null);
                onClearResult?.();
              }}
              sx={{ width: 24, height: 24 }}
            >
              <Iconify icon="mdi:chevron-right" width={16} />
            </IconButton>
          </Box>
        )}

        {/* Variable mapping — skeleton rows stay visible until callDetail
            resolves, so we don't flicker between two loading states. The
            shell (search bar, header, rows area with maxHeight 320) mirrors
            the real table structure so the swap-in is layout-stable. */}
        {isMappingPending && (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                overflow: "hidden",
              }}
            >
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
                  value=""
                  disabled
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
              <Box
                sx={{
                  display: "flex",
                  px: 1.5,
                  py: 0.5,
                  backgroundColor: (theme) =>
                    theme.palette.mode === "dark"
                      ? "rgba(255,255,255,0.03)"
                      : "#fafafa",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  variant="caption"
                  fontWeight={600}
                  sx={{ width: 200, flexShrink: 0 }}
                >
                  Columns
                </Typography>
                <Typography
                  variant="caption"
                  fontWeight={600}
                  sx={{ flex: 1 }}
                >
                  Value
                </Typography>
              </Box>
              <Box sx={{ maxHeight: 320, overflowY: "auto" }}>
                {Array.from({ length: 10 }).map((_, i) => (
                  <Box
                    key={i}
                    sx={{
                      display: "flex",
                      alignItems: "flex-start",
                      px: 1.5,
                      py: 0.6,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      "&:last-child": { borderBottom: "none" },
                    }}
                  >
                    <Skeleton
                      variant="text"
                      width={180}
                      sx={{ flexShrink: 0, pt: 0.25 }}
                    />
                    <Box sx={{ flex: 1, pl: 1.5 }}>
                      <Skeleton variant="text" />
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          )}

        {callDetail &&
          !loadingDetail &&
          !loadingCalls &&
          (() => {
            const previewCallType =
              callDetail.simulation?.call_type ||
              callDetail.call_type ||
              callDetail.simulation_call_type;
            const previewIsText =
              typeof previewCallType === "string" &&
              ["text", "chat", "prompt"].includes(
                previewCallType.toLowerCase(),
              );
            const applicableResolverKeys = new Set(
              previewIsText
                ? [...COMMON_RESOLVER_KEYS, ...TEXT_RESOLVER_KEYS]
                : [...COMMON_RESOLVER_KEYS, ...VOICE_RESOLVER_KEYS],
            );
            return (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px",
                  overflow: "hidden",
                }}
              >
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

                <Box
                  sx={{
                    display: "flex",
                    px: 1.5,
                    py: 0.5,
                    backgroundColor: (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.03)"
                        : "#fafafa",
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{ width: 200, flexShrink: 0 }}
                  >
                    Columns
                  </Typography>
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{ flex: 1 }}
                  >
                    Value
                  </Typography>
                </Box>

                <Box sx={{ maxHeight: 320, overflowY: "auto" }}>
                  {sortEntries(flattenLeaves(callDetail))
                    .filter(([key, val]) => {
                      // Always show applicable resolver-vocabulary keys —
                      // users need to see the full binding surface for this
                      // sim type even when the value is empty for the
                      // current preview call.
                      if (applicableResolverKeys.has(key)) return true;
                      // Hide null/empty values entirely — don't show "—" rows
                      if (val === null || val === undefined || val === "")
                        return false;
                      if (
                        typeof val === "object" &&
                        !Array.isArray(val) &&
                        canonicalKeys(val).length === 0
                      )
                        return false;
                      if (Array.isArray(val) && val.length === 0) return false;
                      return true;
                    })
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
                      const isAudioUrl =
                        (key === "audio_url" ||
                          key === "recording_url" ||
                          key === "stereo_recording_url" ||
                          key === "customer_log_url" ||
                          key === "call.voice_recording" ||
                          key === "call.stereo_recording" ||
                          key === "call.assistant_recording" ||
                          key === "call.customer_recording" ||
                          key === "call.recording_url" ||
                          key === "call.stereo_recording_url") &&
                        typeof val === "string" &&
                        val.startsWith("http");
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
                          <Box
                            sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}
                          >
                            {isEmpty ? (
                              <Typography
                                variant="caption"
                                color="text.disabled"
                              >
                                —
                              </Typography>
                            ) : isAudioUrl ? (
                              <AudioPlaybackProvider>
                                <CustomAudioPlayer
                                  audioData={{ url: val }}
                                  cacheKey={`${currentCall?.id || currentCallIndex}-${key}`}
                                />
                              </AudioPlaybackProvider>
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
                                    WebkitLineClamp: expandedCols[key]
                                      ? 999
                                      : 2,
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
                                  reached for primitives because the
                                  upstream isObj/isArr check routes
                                  objects to JsonValueTree. If something
                                  slips through (e.g. an unflattened
                                  nested struct), JSON.stringify rather
                                  than falling back to "[object Object]". */}
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
                </Box>
              </Box>
            );
          })()}

        {/* Empty state */}
        {selectedExecutionId &&
          !loadingCalls &&
          !isPendingCallsFetch &&
          totalCalls === 0 && (
          <Typography
            variant="body2"
            color="text.disabled"
            sx={{ textAlign: "center", py: 3 }}
          >
            No calls found for this simulation run
          </Typography>
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
                        placeholder="Search field..."
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
            sx={{
              p: 1.5,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "error.main",
              backgroundColor: "error.lighter",
            }}
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

SimulationTestMode.displayName = "SimulationTestMode";

SimulationTestMode.propTypes = {
  templateId: PropTypes.string,
  variables: PropTypes.array,
  codeParams: PropTypes.object,
  onTestResult: PropTypes.func,
  onColumnsLoaded: PropTypes.func,
  onClearResult: PropTypes.func,
  initialMapping: PropTypes.object,
  initialRunTestId: PropTypes.string,
  isComposite: PropTypes.bool,
  compositeAdhocConfig: PropTypes.object,
};

export default SimulationTestMode;
