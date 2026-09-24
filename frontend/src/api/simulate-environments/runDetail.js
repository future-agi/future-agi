import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import useKpis from "src/hooks/useKpis";
import { extractKpis } from "src/sections/test-detail/common";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import { useCallExecutionDetail } from "src/sections/agents/helper";
import { runColor } from "src/sections/simulate/environments/workspace/runs/runs.constants";
import { listRunTestExecutions, mapExecutions } from "./runs";

/**
 * The run/execution DETAIL data source.
 *
 * The designer's run views (header, per-call table, call drawer) consume two
 * fixed view-model shapes — `identity` and `stats` — produced in the mock by
 * `runSummaries`. This module produces those same shapes from REAL product
 * payloads so the ported components render unchanged:
 *   - `identity` from the matching `detailExecutions` row (reusing
 *     `executionToRun` + `mapExecutions` for the ordinal, so the list, the
 *     detail header and any comparison agree on one number/letter/colour).
 *   - `stats` from `test-executions/{id}/kpis/` (call counts, duration, eval
 *     scores) and `…/performance-summary/` (pass rate).
 *
 * The adapters are pure and separately exported for unit tests. Later phases
 * add the per-call table (`useRunCalls`) and the call drawer (`useCallDetail`);
 * their output shapes are fixed here as JSDoc typedefs and stubbed so callers
 * can be written against the final contract now.
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
 * @property {"passed"|"failed"|"running"} status  Run-level outcome.
 */

/**
 * @typedef {Object} RunStats
 * @property {number} total         Calls/chats run (`kpis.total_calls`).
 * @property {number} passed        `total - failed`.
 * @property {number} failed        `kpis.failed_calls`.
 * @property {?number} completed    P19's `completed_calls` straight from the
 *                                  KPI body, both modalities (TH-8046); null
 *                                  while the KPIs haven't loaded or the field
 *                                  is absent — never 0, never `total`.
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
 * @property {number} failedCritical 0 — GAP: no per-task `critical` flag at the
 *                                  run level; the critical banner stays inert
 *                                  until the per-call feed (Phase 2) lands.
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
  const total = kpis?.total_calls ?? row?.total ?? 0;
  const failed = kpis?.failed_calls ?? row?.failed ?? 0;
  const passed = Math.max(total - failed, 0);

  const perfRate = perf?.test_run_performance_metrics?.pass_rate;
  const passRate =
    typeof perfRate === "number"
      ? Math.round(perfRate)
      : total
        ? Math.round((passed / total) * 100)
        : 0;

  // P27 (contract v1.8, owner's ruling 2026-09-23 night; round-3 M2): the KPI
  // body now exposes `completed_calls` for BOTH modalities — TH-8046 adds
  // `"completed_calls": metrics.get("completed_calls", 0) or 0` to
  // `RunTestKPIsView`'s `kpi_data`, off the column P19 names,
  // `COUNT(*) FILTER (WHERE status = 'completed')` (`sql_query.py:368`). So
  // this reads one field instead of borrowing the chat branch's
  // `connected_calls` (which on a voice run is `connected_voice_calls`,
  // `duration_seconds > 0` — a different filter), and a voice run finally gets
  // the count P27 promises before the click.
  //
  // It stays a different number from `total` (COUNT(*) over every status,
  // `sql_query.py:364`), and it stays `null` — never `0`, never `total` —
  // whenever the KPIs have not loaded or the field is absent (an older backend,
  // before TH-8046 lands). "Not known yet" must never be shown as a number that
  // means something else.
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
    measured: total,
    unmeasured: 0,
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
  const rowsQuery = useQuery({
    queryKey: ["run-test-executions", runTestId],
    queryFn: () => listRunTestExecutions(runTestId),
    enabled: !!runTestId,
    select: mapExecutions,
  });

  const kpisQuery = useKpis(executionId);

  const perfQuery = useQuery({
    queryKey: ["test-execution-detail", "PERFORMANCE_SUMMARY", executionId],
    queryFn: () =>
      axios
        .get(endpoints.testExecutions.executionPerformanceSummary(executionId))
        .then((res) => res.data),
    enabled: !!executionId,
    staleTime: 1000 * 60 * 5,
  });

  const row =
    (rowsQuery.data ?? []).find((r) => r.executionId === executionId) ?? null;

  // Memoised so `identity`/`stats` keep a stable reference across renders — a
  // later phase's table/drawer can safely put them in effect/memo deps.
  const identity = useMemo(
    () => buildRunIdentity(row, envName ?? null),
    [row, envName],
  );
  const stats = useMemo(
    () => buildRunStats(kpisQuery.data, perfQuery.data, row),
    [kpisQuery.data, perfQuery.data, row],
  );

  const isLoading =
    (!!runTestId && rowsQuery.isLoading) ||
    (!!executionId && (kpisQuery.isPending || perfQuery.isLoading));

  return { identity, stats, isLoading };
}

/**
 * @typedef {Object} RunTask
 * A single row of the per-call table (Phase 2). Sourced from
 * `testExecutions.list(executionId)`.
 * @property {string} id           Call-execution id (opens the call drawer).
 * @property {string} scenario     Scenario / task name.
 * @property {?string} persona     Simulated-user persona label.
 * @property {"passed"|"failed"|"flaky"|"error"|"unmeasured"} status
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
 *   threshold?: number }>} evalResults  Per-eval cells for this call.
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
 *   `eval_metrics`; `removed` is true for a verdict whose eval was removed (P23).
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
    data.status === "pending" || data.status === "skipped" || data.skipped || data.error;

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
    // §7 P23: the verdict of an eval that has since been removed from the
    // environment is still returned, carrying `removed: true`. It is never
    // hidden and never rewritten — the drawer marks it (P28).
    removed: data.removed === true,
  };
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
  const turns = (Array.isArray(raw.transcript) ? raw.transcript : []).map((t) => ({
    role: normalizeRole(t.speaker_role ?? t.role),
    text: t.content ?? "",
    at: t.start_time_seconds ?? null,
    toolCalls: t.tool_calls ?? null,
  }));

  const words = turns.reduce(
    (a, s) => a + (s.text ? s.text.trim().split(/\s+/).filter(Boolean).length : 0),
    0,
  );
  const toolCalls = turns.filter(
    (s) => Array.isArray(s.toolCalls) && s.toolCalls.length > 0,
  ).length;

  const aiPct = raw.agent_talk_percentage ?? null;
  const evalMetrics = raw.eval_metrics && typeof raw.eval_metrics === "object" ? raw.eval_metrics : {};
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
    recordings: raw.recordings && typeof raw.recordings === "object" ? raw.recordings : {},
    evalResults,
  };
}

/**
 * The chat call drawer data source. Reuses the product's
 * `useCallExecutionDetail` (same `/simulate/call-executions/{id}/` read, shared
 * cache) and adapts the payload to the `CallDetail` view-model.
 * @param {?string} callExecId
 * @returns {{ callDetail: ?CallDetail, isLoading: boolean }}
 */
export function useCallDetail(callExecId) {
  const query = useCallExecutionDetail(callExecId, !!callExecId);
  const callDetail = useMemo(() => mapCallDetail(query.data), [query.data]);
  return { callDetail, isLoading: !!callExecId && query.isPending };
}
