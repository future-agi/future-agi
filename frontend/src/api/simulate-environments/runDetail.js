import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { extractKpis } from "src/sections/test-detail/common";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import {
  ACTIVE_EXECUTION_STATUSES,
  STOPPABLE_EXECUTION_STATUSES,
  runColor,
} from "src/sections/simulate/environments/workspace/runs/runs.constants";
import useKpis from "src/hooks/useKpis";

/**
 * The run/execution DETAIL data source.
 *
 * The run views consume fixed `identity` and `stats` view models. The active
 * hook builds them from the isolated v3 run-results contract; the pure legacy
 * adapters remain exported for their existing compatibility tests.
 */

/**
 * @typedef {Object} RunIdentity
 * @property {string} id            The execution id.
 * @property {string} executionId   Same as `id` (routing convenience).
 * @property {number} ordinal       "Run N" — stamped from newest-first order.
 * @property {string} letter        `String(ordinal)`; the identity-chip glyph.
 * @property {string} color         Ordinal-keyed identity colour (hex).
 * @property {?string} name         The environment name (from `envName` opt).
 * @property {?string} agentVersion Agent version the run was recorded against.
 * @property {?string} startedAt    ISO start time.
 * @property {?string} finishedAt   ISO finish time — GAP: the executions row
 *                                   carries no end time, so this is null.
 * @property {"passed"|"failed"|"running"|"cancelling"|"cancelled"} status  Run-level outcome.
 */

/**
 * @typedef {Object} RunStats
 * @property {number} total         Calls/chats run (`kpis.total_calls`).
 * @property {number} passed        `total - failed`.
 * @property {number} failed        `kpis.failed_calls`.
 * @property {?number} completed    `kpis.completed_calls`, both modalities;
 *                                  null while the KPIs haven't loaded or the
 *                                  field is absent — never 0, never `total`.
 * @property {number} passRate      0–100; `performance-summary.pass_rate` when
 *                                  present, else derived `passed/total`.
 * @property {?number} durationS    Total wall-clock seconds (`total_duration`).
 * @property {?number} avgDurationMs Mean per-call duration in ms (derived).
 * @property {?number} avgScore     `kpis.avg_score` (0–100), null if absent.
 * @property {?number} csat         `kpis.avg_csat_score`, null if absent.
 * @property {?number} avgTurnCount `kpis.avg_turn_count`, null if absent.
 * @property {?number} connectedPct `kpis.calls_connected_percentage`.
 * @property {?string} agentType    `kpis.agent_type` ("voice"|"text") — the
 *                                  run-level voice-vs-chat signal the call drawer
 *                                  falls back to when a row omits its own type.
 * @property {?number} tokens       GAP: no run-level token total in kpis → null.
 * @property {?number} cost         GAP: no run-level cost in kpis → null.
 * @property {Object<string, number>} scores  {evalId: 0–100} from kpis eval
 *                                  metrics (the per-call table's columns).
 * @property {number} measured      Calls that produced a verdict (= total).
 * @property {number} unmeasured    0 — no per-call verdict feed at run level.
 * @property {number} flaky         0 — GAP: no flaky signal in kpis.
 * @property {number} dropped       0 — GAP: no dropped-scenario signal.
 */

/**
 * Builds the identity chip / header view-model from one mapped execution row.
 * Pure.
 * @param {Object} row     A row from `mapExecutions` (carries `ordinal`).
 * @param {?string} envName The environment name for the sub-line.
 * @returns {?RunIdentity}
 */
export function buildRunIdentity(row, envName = null) {
  if (!row) return null;
  return {
    id: row.id,
    executionId: row.executionId,
    ordinal: row.ordinal,
    letter: String(row.ordinal),
    color: runColor(row.ordinal),
    name: envName,
    agentVersion: row.agentVersion ?? null,
    startedAt: row.startedAt ?? null,
    finishedAt: row.finishedAt ?? null,
    status: row.status,
    scenarioIds: row.scenarioIds ?? [],
    trials: row.trials ?? 1,
  };
}

/**
 * Builds the run-level stats view-model from the real kpis + performance
 * summary payloads, falling back to the executions row for the call counts.
 * Pure.
 * @param {?Object} kpis The `test-executions/{id}/kpis/` payload.
 * @param {?Object} perf The `…/performance-summary/` payload.
 * @param {?Object} row  The mapped executions row (count fallback).
 * @returns {RunStats}
 */
export function buildRunStats(kpis, perf, row) {
  const total = row?.hasOutcomes
    ? row.total
    : kpis?.total_calls ?? row?.total ?? 0;
  const failed = row?.hasOutcomes
    ? row.failed
    : kpis?.failed_calls ?? row?.failed ?? 0;
  const passed = row?.hasOutcomes ? row.passed : Math.max(total - failed, 0);

  const perfRate = perf?.test_run_performance_metrics?.pass_rate;
  const passRate =
    row?.hasOutcomes || typeof perfRate !== "number"
      ? total
        ? Math.round((passed / total) * 100)
        : 0
      : Math.round(perfRate);

  // `completed_calls` is its own KPI field for both modalities — not the chat
  // branch's `connected_calls` (voice's `connected_voice_calls` uses a
  // different filter, `duration_seconds > 0`), and not `total`, which counts
  // every status. Stays `null` — never `0`, never `total` — until the KPIs
  // load or on an older backend where the field is absent: "not known yet"
  // must never look like a real number.
  const completed = kpis?.completed_calls ?? null;

  const durationS = kpis?.total_duration ?? null;
  const avgDurationMs =
    durationS != null && total ? Math.round((durationS * 1000) / total) : null;

  // Eval columns for the per-call table. `extractKpis` sorts the payload's
  // numeric non-system keys into `evalMetrics`, already on a 0–100 scale (it is
  // what the product feeds straight into a LinearProgress). Semantically these
  // are per-eval AVERAGE scores rather than the designer's pass-percentage, but
  // the scale matches so the ported column renders unchanged.
  const { evalMetrics } = extractKpis(kpis || {}, kpis?.agent_type);

  return {
    total,
    passed,
    failed,
    completed,
    passRate,
    durationS,
    avgDurationMs,
    avgScore: kpis?.avg_score ?? null,
    csat: kpis?.avg_csat_score ?? null,
    avgTurnCount: kpis?.avg_turn_count ?? null,
    connectedPct: kpis?.calls_connected_percentage ?? null,
    agentType: kpis?.agent_type ?? null,
    tokens: null,
    cost: null,
    scores: { ...evalMetrics },
    measured: row?.hasOutcomes ? passed + failed : total,
    unmeasured: row?.hasOutcomes ? Math.max(total - passed - failed, 0) : 0,
    flaky: 0,
    dropped: 0,
    failedCritical: 0,
  };
}

/**
 * The run-detail header + stats hook.
 * @param {string} runTestId   The run-test id (owns the executions list).
 * @param {string} executionId The execution row to detail.
 * @param {{ envName?: string }} [opts]
 * @returns {{ identity: ?RunIdentity, stats: RunStats, isLoading: boolean }}
 */
export function useRunDetail(runTestId, executionId, { envName } = {}) {
  const query = useQuery({
    queryKey: ["simulation-run-results-v3", executionId, "summary"],
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.calls(executionId), {
          params: { page: 1, page_size: 1 },
        })
        .then((res) => res.data),
    enabled: !!executionId,
    refetchInterval: (query) =>
      ACTIVE_EXECUTION_STATUSES.has(query.state.data?.execution?.status)
        ? 3000
        : false,
    staleTime: 1000 * 60 * 5,
  });
  // `completed` is its own KPI field, cached under the key `useRunsSummary`
  // primes (`useKpis`), so reading it here shares that entry rather than
  // refetching. Stays null — never 0 — until the KPIs load, per the RunStats
  // contract. Everything else on `stats` comes from the run-results summary.
  const kpisQuery = useKpis(executionId);
  const execution = query.data?.execution;
  const summary = execution?.summary;
  const identity = useMemo(() => {
    if (!execution) return null;
    return {
      id: execution.id,
      executionId: execution.id,
      ordinal: execution.ordinal,
      letter: String(execution.ordinal),
      color: runColor(execution.ordinal),
      name: envName ?? null,
      agentVersion: execution.agent_version ?? null,
      startedAt: execution.started_at ?? null,
      status:
        execution.status === "cancelling"
          ? "cancelling"
          : ACTIVE_EXECUTION_STATUSES.has(execution.status)
            ? "running"
            : execution.status === "failed"
              ? "failed"
              : execution.status === "cancelled"
                ? "cancelled"
                : summary?.outcomes?.passed > 0
                  ? "passed"
                  : "failed",
      stoppable: STOPPABLE_EXECUTION_STATUSES.has(execution.status),
      scenarioIds: execution.selected_scenario_keys?.length
        ? execution.selected_scenario_keys
        : undefined,
      trials: execution.trials ?? 1,
    };
  }, [execution, envName, summary]);
  const stats = useMemo(
    () => ({
      total: summary?.total ?? 0,
      passed: summary?.outcomes?.passed ?? 0,
      failed:
        (summary?.outcomes?.failed ?? 0) + (summary?.outcomes?.error ?? 0),
      completed: kpisQuery.data?.completed_calls ?? null,
      passRate: summary?.pass_rate ?? 0,
      durationS: summary?.duration?.average ?? null,
      avgDurationMs:
        summary?.duration?.average != null
          ? summary.duration.average * 1000
          : null,
      avgScore: null,
      csat: null,
      avgTurnCount: null,
      connectedPct: null,
      agentType: execution?.agent_type ?? null,
      tokens: summary?.tokens?.total_value ?? null,
      cost:
        summary?.cost_cents?.total_value != null
          ? summary.cost_cents.total_value / 100
          : null,
      scores: {},
      measured: summary?.measured ?? 0,
      unmeasured: summary?.outcomes?.inconclusive ?? 0,
      flaky: 0,
      dropped: 0,
      failedCritical: 0,
    }),
    [execution, summary, kpisQuery.data],
  );

  const isLoading = !!executionId && query.isPending;

  return { identity, stats, isLoading, error: query.error };
}

/**
 * @typedef {Object} RunTask
 * A single row of the per-call table (Phase 2). Sourced from
 * `testExecutions.list(executionId)`.
 * @property {string} id           Call-execution id (opens the call drawer).
 * @property {string} scenario     Scenario / task name.
 * @property {?string} persona     Simulated-user persona label.
 * @property {"passed"|"failed"|"flaky"|"error"|"unmeasured"} status
 * @property {?string} harnessOutcomeStatus Authoritative sealed trial verdict.
 * @property {?string} executionStatus Transport lifecycle status; kept separate.
 * @property {boolean} critical    Whether the scenario is a release blocker.
 * @property {?number} csat        Per-call CSAT, on the product's 0–10 scale
 *                                 (`overall_score`); null when absent.
 * @property {?number} turns       Turn count.
 * @property {?number} latencyMs   Mean latency, ms.
 * @property {?number} tokens      Token total.
 * @property {?number} durationMs  Call duration, ms.
 * @property {?string} simulationCallType  "voice" | "text" — routes the call
 *                                 drawer to the voice vs chat branch.
 * @property {?string} provider    Call provider (vapi/retell/livekit/…).
 * @property {Array<{ id: string, name: string, score: number, passed: boolean,
 *   threshold?: number, errored?: boolean }>} evalResults  Per-eval cells for
 *   this call; `errored` marks one whose scoring failed.
 * @property {boolean} csatFailed  CSAT scoring failed and left no score.
 * @property {?string} csatError   Why CSAT scoring failed.
 * @property {{evals: boolean, csat: boolean, metrics: boolean}} pending
 *                                 Which empty values are still coming: evals
 *                                 and CSAT while scoring, turns and latency
 *                                 while the call runs.
 */

/**
 * @typedef {Object} TraceColumn
 * A per-call table column descriptor (mirrors the designer's TraceTable).
 * @property {string} key       Stable column key.
 * @property {string} label     Header label.
 * @property {boolean} defaultOn Whether shown by default.
 * @property {number} [width]    Fixed width, px.
 * @property {string} [group]    Column-group heading.
 */

// The per-call table data source (Phase 2). Implemented in `runCalls.js` against
// the product's real `testExecutions.list` payload; re-exported here so callers
// keep importing the run-detail data hooks from one module.
export { useRunCalls, mapCallRow, buildTraceColumns } from "./runCalls";

// The fix-my-agent (Debug-failures) data source (Phase 4): the run diagnosis and
// the past-optimization runs list, both over REAL product endpoints. Re-exported
// so callers import every run-detail data hook from one module.
export {
  useOptimizerAnalysis,
  mapOptimizerAnalysis,
  useOptimizationRuns,
  mapOptimizationRuns,
  ANALYSIS_WORKING_STATES,
} from "./optimizer";

/**
 * @typedef {Object} CallDetail
 * The call drawer view-model (Phase 3). Sourced from the REAL
 * `testExecutions.callDetail(callExecId)` = `GET /simulate/call-executions/{id}/`
 * (`CallExecutionDetailSerializer`).
 * @property {string} id
 * @property {"voice"|"chat"} type   Modality — `simulation_call_type`.
 * @property {?string} direction     "Inbound"|"Outbound" (`call_type`); voice only.
 * @property {?string} phone         `phone_number`.
 * @property {?string} provider      `provider`.
 * @property {?number} durationS     `duration` (seconds).
 * @property {Array<{ role: "agent"|"customer"|string, text: string,
 *   at: ?number, toolCalls: ?any }>} turns  From `transcript`
 *   (voice `CallTranscriptSerializer`, chat `ChatMessageSerializer`).
 * @property {{ turnCount: ?number, latencyMs: ?number, userPct: ?number,
 *   aiPct: ?number, words: number, silenceS: ?number, ttfwMs: ?number,
 *   toolCalls: number }} stats  `silenceS`/`ttfwMs` have no backend field (GAP).
 * @property {?number} tokens        `total_tokens` (null for most voice runs).
 * @property {?number} cost          Dollars from `cost_cents` (null when absent).
 * @property {?string} summary       `call_summary`.
 * @property {Object<string,string>} recordings  `recordings` map (voice audio).
 * @property {Array<{ id: string, name: string, score: ?number,
 *   passed: ?boolean, reason: string, removed: boolean }>} evalResults  From
 *   `eval_metrics`; `removed` is true for a verdict whose eval was removed.
 */

// Normalise a transcript/chat role to the two the drawer paints. Voice
// `speaker_role` and chat `role` share the same vocabulary
// (user|assistant|system|tool_calls|tool_call_result|unknown); the assistant is
// the agent under test, the user is the simulated customer. Other roles pass
// through so a system/tool row is never silently mislabelled as a speaker.
function normalizeRole(role) {
  const r = String(role || "").toLowerCase();
  if (r === "assistant" || r === "agent" || r === "bot") return "agent";
  if (r === "user" || r === "customer" || r === "human") return "customer";
  return r;
}

const displayJson = (value) =>
  typeof value === "string" ? value : JSON.stringify(value);

const finiteNumber = (value) => {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

// Explicit relative offsets can legitimately be zero. Harness `at` is an epoch
// timestamp, with zero meaning unknown, so it must not be read as an offset.
function relativeTime(row, end = false) {
  // The serializer uses null (and stored end_time_ms=0) for unknown ends.
  if (end && row.end_time_seconds === null) return null;
  const seconds = end
    ? [row.end_time_seconds, row.completed_at_seconds]
    : [row.start_time_seconds, row.started_at_seconds];
  for (const value of seconds) {
    const number = finiteNumber(value);
    if (number != null) return number;
  }
  const milliseconds = finiteNumber(end ? row.end_time_ms : row.start_time_ms);
  if (end && milliseconds <= 0) return null;
  return milliseconds == null ? null : milliseconds / 1000;
}

export function functionCallTranscriptRows(calls = []) {
  return (Array.isArray(calls) ? calls : []).map((call, index) => {
    const duration = call.duration_ms ?? call.durationMs;
    const heading = `Function call · ${call.name || call.function?.name || "tool"}${duration != null ? ` · ${duration}ms` : ""}`;
    const args = call.arguments ?? call.function?.arguments;
    const result = call.result ?? call.output;
    return {
      id: call.id || `function-call-${index}`,
      speaker_role: "tool",
      content: [
        heading,
        args != null ? `→ args: ${displayJson(args)}` : null,
        result != null ? `← result: ${displayJson(result)}` : null,
      ]
        .filter(Boolean)
        .join("\n"),
      start_time_seconds: relativeTime(call),
      end_time_seconds: relativeTime(call, true),
      tool_calls: [call],
    };
  });
}

/** One stable timeline shared by the chat and voice drawers. */
export function callTranscript(raw) {
  const transcript = Array.isArray(raw?.transcript) ? raw.transcript : [];
  return [
    ...transcript.map((turn) => ({
      ...turn,
      start_time_seconds: relativeTime(turn),
      end_time_seconds: relativeTime(turn, true),
    })),
    ...functionCallTranscriptRows(raw?.function_calls),
  ].sort((a, b) => {
    const startA = a.start_time_seconds;
    const startB = b.start_time_seconds;
    if (startA == null) return startB == null ? 0 : 1;
    if (startB == null) return -1;
    return startA - startB;
  });
}

// One eval cell from an `eval_metrics[evalId]` entry, reusing the product's
// `normalizeEvalResult` (the same path `runCalls` uses for the table). Pending /
// skipped / errored evals carry no verdict (`passed: null`) so they never feed
// the failed-eval banner.
function callEvalResult(evalId, data) {
  if (!data || typeof data !== "object") return null;
  const norm = normalizeEvalResult(data.value, data.type);
  // Pending evals serialise as `{}` and errored ones may carry no value — both
  // normalise to "empty" and have no cell to show.
  if (norm.kind === "empty") return null;
  const inertStatus =
    data.status === "pending" ||
    data.status === "skipped" ||
    data.skipped ||
    data.error;

  const to01 = (n) => (n == null ? null : n <= 1 ? n : n / 100);
  let score = null;
  let passed = null;
  if (norm.kind === "score") {
    score = to01(norm.score);
    passed = inertStatus || score == null ? null : score >= 0.5;
  } else if (norm.kind === "passfail") {
    passed = inertStatus ? null : norm.pass;
    score = norm.pass == null ? null : norm.pass ? 1 : 0;
  } else if (norm.kind === "choices") {
    score = to01(norm.score);
  }

  return {
    id: evalId,
    name: data.name || evalId,
    score,
    passed,
    reason: data.reason || "",
    // A removed eval's verdict is still returned, carrying `removed: true` —
    // never hidden or rewritten; the drawer marks it.
    removed: data.removed === true,
  };
}

// A transcript row's text. Voice rows carry a string; a hosted chat row's
// `content` is a list of OpenAI-style `{role, content}` parts (strings or
// `{text}` also occur). Always returns a string.
function messageText(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map(messageText).filter(Boolean).join("\n");
  }
  if (typeof content === "object") {
    return messageText(content.content ?? content.text ?? "");
  }
  return String(content);
}

/**
 * Maps one raw `call-executions/{id}/` payload → a `CallDetail`. Pure and
 * exported for unit tests.
 * @param {?Object} raw
 * @returns {?CallDetail}
 */
export function mapCallDetail(raw) {
  if (!raw) return null;

  const isChat = raw.simulation_call_type === "text";
  const turns = callTranscript(raw).map((t) => ({
    role: normalizeRole(t.speaker_role ?? t.role),
    text: messageText(t.content),
    at: t.start_time_seconds ?? null,
    toolCalls: t.tool_calls ?? null,
  }));

  const words = turns.reduce(
    (a, s) =>
      a + (s.text ? s.text.trim().split(/\s+/).filter(Boolean).length : 0),
    0,
  );
  const toolCalls = turns.reduce(
    (count, turn) =>
      count + (Array.isArray(turn.toolCalls) ? turn.toolCalls.length : 0),
    0,
  );

  const aiPct = raw.agent_talk_percentage ?? null;
  const evalMetrics =
    raw.eval_metrics && typeof raw.eval_metrics === "object"
      ? raw.eval_metrics
      : {};
  const evalResults = Object.entries(evalMetrics)
    .map(([id, data]) => callEvalResult(id, data))
    .filter(Boolean);

  return {
    id: raw.id,
    type: isChat ? "chat" : "voice",
    direction: raw.call_type ?? null,
    phone: raw.phone_number ?? null,
    provider: raw.provider ?? null,
    durationS: raw.duration ?? raw.duration_seconds ?? null,
    turns,
    stats: {
      turnCount: raw.turn_count ?? null,
      latencyMs: raw.avg_agent_latency ?? raw.avg_latency_ms ?? null,
      aiPct,
      userPct: aiPct == null ? null : Math.round((100 - aiPct) * 10) / 10,
      words,
      // No backend field for either — GAP, MockBadged in the drawer.
      silenceS: null,
      ttfwMs: null,
      toolCalls,
    },
    tokens: raw.total_tokens ?? null,
    cost: raw.cost_cents != null ? raw.cost_cents / 100 : null,
    summary: raw.call_summary ?? null,
    recordings:
      raw.recordings && typeof raw.recordings === "object"
        ? raw.recordings
        : {},
    evalResults,
  };
}

/**
 * The raw v3 call-detail data source shared by the voice and chat drawers.
 * @param {?string} callExecId
 * @param {boolean} [enabled]
 */
export function useCallExecutionV3Detail(callExecId, enabled = true) {
  return useQuery({
    queryKey: ["simulation-call-detail-v3", callExecId],
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.callDetail(callExecId))
        .then((response) => response.data),
    enabled: enabled && !!callExecId,
    staleTime: 1000 * 60 * 5,
  });
}

/**
 * The chat call drawer data source. Adapts the v3 response to the CallDetail
 * view-model while sharing its query cache with the voice drawer.
 * @param {?string} callExecId
 * @returns {{ callDetail: ?CallDetail, isLoading: boolean }}
 */
export function useCallDetail(callExecId) {
  const query = useCallExecutionV3Detail(callExecId);
  const callDetail = useMemo(() => mapCallDetail(query.data), [query.data]);
  return {
    callDetail,
    isLoading: !!callExecId && query.isPending,
    error: query.error,
  };
}
