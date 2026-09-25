import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import { TRACE_COLUMNS } from "src/sections/simulate/environments/workspace/runs/detail/trace/traceTable.constants";
import { ACTIVE_EXECUTION_STATUSES } from "src/sections/simulate/environments/workspace/runs/runs.constants";

/** Adapt the v3 run-results contract for the trace table. */

const to01 = (n) => (n == null ? null : n <= 1 ? n : n / 100);

// The backend marks an eval whose scoring broke as "Failed" (or "error");
// it has no score, only the reason it failed.
const ERRORED_EVAL_STATUSES = new Set(["failed", "error"]);

// One cell from a live `evaluations[]` entry — the array shape the isolated
// v3 run-results contract sends, where the server has already scored the eval.
function liveEvalCell(col, data) {
  const stored =
    data.value && typeof data.value === "object" ? storedEvalCell(col, data) : null;
  return {
    id: col.id,
    name: data.name || col.name || col.column_name || col.id,
    score: data.score ?? stored?.score ?? to01(data.value?.score) ?? null,
    passed: data.passed ?? stored?.passed ?? null,
    label: typeof data.value === "string" ? data.value : (stored?.label ?? null),
    reason: data.reason || "",
    threshold: 0.5,
    removed: data.removed === true,
    errored: ERRORED_EVAL_STATUSES.has(String(data.status ?? "").toLowerCase()),
  };
}

// One cell from an `eval_metrics[evalId]` entry, normalised through the
// product's `normalizeEvalResult` — the eval-picker path, which also carries a
// since-removed eval's stored verdict marked `removed: true` so the chat call
// drawer's list-derived fallback (while `useCallDetail` is loading or errored)
// can mark it too — same expression as `runDetail.js`'s `callEvalResult`.
function storedEvalCell(col, data) {
  const norm = normalizeEvalResult(data.value, data.type ?? col.eval_config?.output);
  if (norm.kind === "empty") return null;
  let score = null;
  let passed = null;
  let label = null;
  if (norm.kind === "score") {
    score = to01(norm.score);
    passed = score != null ? score >= 0.5 : null;
  } else if (norm.kind === "passfail") {
    passed = norm.pass;
    score = passed == null ? null : passed ? 1 : 0;
    label = norm.label ?? null;
  } else if (norm.kind === "choices") {
    score = to01(norm.score);
    label = (norm.items || []).join(", ") || null;
  }
  return {
    id: col.id,
    name: data.name || col.column_name || col.name || col.id,
    score,
    passed,
    label,
    reason: data.reason || "",
    threshold: 0.5,
    removed: data.removed === true,
  };
}

// The server owns status, filtering, grouping, and evaluation normalization.
// A live verdict (the `evaluations[]` array) beats a stored one; a since-removed
// eval only survives in `eval_metrics`, keyed by config id, so fall to it there.
function evalResultFor(row, col) {
  const live = row?.evaluations?.find((item) => item.id === col.id);
  if (live) return liveEvalCell(col, live);
  const stored = row?.eval_metrics?.[col.id];
  if (stored) return storedEvalCell(col, stored);
  return null;
}

// A value still coming stops loading this long after its call ended, so a
// stuck scoring job can't load or poll forever.
export const PENDING_VALUE_STALE_MS = 10 * 60 * 1000;

const SCORING_CSAT_STATUSES = new Set(["pending", "running"]);
const IN_PROGRESS_CALL_STATUSES = new Set([
  "pending",
  "queued",
  "ongoing",
  "analyzing",
]);

function isFresh(row, now) {
  const at = row?.completed_at ?? row?.started_at;
  if (!at) return false;
  const time = new Date(at).getTime();
  return Number.isFinite(time) && now - time < PENDING_VALUE_STALE_MS;
}

// The backend has no per-eval status, so a call's empty eval cells load
// together while the call is being scored.
export function isEvalScoring(row, now) {
  return (
    row?.eval_started === true &&
    row?.eval_completed !== true &&
    isFresh(row, now)
  );
}

export function isCsatScoring(row, now) {
  return SCORING_CSAT_STATUSES.has(row?.csat_status) && isFresh(row, now);
}

// A stopped run halts its scoring jobs, so a call it left mid-scoring will
// never finish; a completed run can still be scoring.
const STOPPED_RUN_STATUSES = new Set(["cancelling", "cancelled", "failed"]);

// The run's status is the cap here: a call left in progress after its run
// ended will never fill in.
export function isCallInProgress(row, runActive) {
  return IN_PROGRESS_CALL_STATUSES.has(row?.execution_status) && !!runActive;
}

/**
 * Maps one raw call row → a `RunTask`. Pure.
 * @param {Object} row          A `results[]` row from `testExecutions.list`.
 * @param {Array} evalColumns   The `column_order` entries of type "evaluation".
 * @param {{ now?: number, runActive?: boolean, runStopped?: boolean }} [opts]
 *   Decide which empty values are still coming.
 * @returns {import("./runDetail").RunTask}
 */
export function mapCallRow(
  row,
  evalColumns = [],
  { now = Date.now(), runActive = false, runStopped = false } = {},
) {
  const evalResults = evalColumns
    .map((col) => evalResultFor(row, col))
    .filter(Boolean);

  const outcome = row?.outcome;
  const status =
    outcome === "inconclusive" || outcome == null ? "unmeasured" : outcome;

  const trialIndex = row?.trial_index ?? null;
  const scenarioName =
    row?.source_scenario_key ||
    row?.scenario ||
    row?.customer_name ||
    "Untitled scenario";
  return {
    id: row?.id,
    goal: row?.goal || row?.scenario || "Untitled goal",
    subGoals: row?.sub_goals ?? [],
    scenario: trialIndex
      ? `${scenarioName} · Trial ${trialIndex}`
      : scenarioName,
    sourceScenario: scenarioName,
    harnessOutcomeStatus: row?.harness_outcome_status ?? outcome ?? null,
    trialIndex,
    scenarioDetails: row?.scenario_details ?? null,
    idealOutcome: row?.ideal_outcome ?? null,
    conversationBranch: row?.conversation_branch ?? null,
    persona: row?.persona || row?.customer_name || null,
    personaDetails: row?.persona_details ?? {
      name: row?.persona || row?.customer_name || null,
      voice: null,
      age: null,
      traits: [],
    },
    status,
    executionStatus: row?.execution_status ?? row?.status ?? null,
    critical: false,
    csat: row?.csat != null ? Math.round(row.csat * 10) / 10 : null,
    csatFailed: row?.csat == null && row?.csat_status === "failed",
    csatError: row?.csat_error ?? null,
    turns: row?.turn_count ?? null,
    latencyMs: row?.latency_ms ?? row?.avg_agent_latency ?? null,
    tokens: row?.tokens ?? row?.total_tokens ?? null,
    durationMs:
      row?.duration_seconds != null
        ? Math.round(row.duration_seconds * 1000)
        : row?.duration != null
          ? Math.round(row.duration * 1000)
          : null,
    // Routing hints for the call drawer.
    simulationCallType: row?.modality ?? row?.simulation_call_type ?? null,
    provider: row?.provider ?? null,
    evalResults,
    // A running call's evals and (voice-only) CSAT are still to come too.
    pending: {
      evals:
        (!runStopped && isEvalScoring(row, now)) ||
        isCallInProgress(row, runActive),
      csat:
        (!runStopped && isCsatScoring(row, now)) ||
        (isCallInProgress(row, runActive) &&
          (row?.modality ?? row?.simulation_call_type) === "voice"),
      metrics: isCallInProgress(row, runActive),
    },
  };
}

/**
 * Builds the `TraceColumn[]` descriptor list from the payload's `column_order`.
 * The fixed system columns come from the picker vocabulary; one column is
 * appended per real evaluation column (keyed by the eval id so it matches
 * `RunTask.evalResults[].id`). Ideal-outcome / conversation-branch columns from
 * the designer are dropped — the executions payload carries no per-call field
 * for them. Pure.
 * @param {Array} columnOrder
 * @returns {import("./runDetail").TraceColumn[]}
 */
export function buildTraceColumns(columnOrder = []) {
  const staticCols = TRACE_COLUMNS.filter((c) => c.key !== "evals").map(
    (c) => ({
      key: c.key,
      label: c.label,
      defaultOn: c.defaultOn,
      width: c.width,
      group: c.group,
    }),
  );
  const evalCols = (columnOrder || []).map((c) => ({
    key: c.id,
    label: c.name || c.id,
    defaultOn: true,
    width: 150,
    group: "Evaluations",
  }));
  return [...staticCols, ...evalCols];
}

/**
 * The calls-list request for one page, as react-query options. The table hook
 * and the call drawer's page-crossing fetch both build it here, so they always
 * share one cache entry per page.
 * @param {string} executionId
 * @param {{ page?: number, limit?: number, search?: string, filters?: Object, groupBy?: string }} [opts]
 */
export function runCallsQueryOptions(executionId, opts = {}) {
  const {
    page = 1,
    limit = 100,
    search = "",
    filters = {},
    groupBy = "goal",
  } = opts;
  return {
    queryKey: [
      "simulation-run-results-v3",
      executionId,
      page,
      limit,
      search,
      filters,
      groupBy,
    ],
    queryFn: () =>
      axios
        .get(endpoints.runResultsV3.calls(executionId), {
          params: {
            page,
            page_size: limit,
            search,
            filters: JSON.stringify(filters),
            group_by: groupBy,
          },
        })
        .then((response) => response.data),
    staleTime: 1000 * 60,
  };
}

/**
 * The per-call table hook. Reads the product's real executions list and adapts
 * it to `{ tasks, columns, count }`. `opts` mirror the product grid's params.
 * @param {string} executionId
 * @param {{ page?: number, limit?: number, search?: string, filters?: Object, groupBy?: string, enabled?: boolean }} [opts]
 * @returns {{ tasks: import("./runDetail").RunTask[],
 *   columns: import("./runDetail").TraceColumn[], count: number,
 *   isLoading: boolean }}
 */
export function useRunCalls(executionId, opts = {}) {
  const { enabled = true, ...listOpts } = opts;
  const query = useQuery({
    ...runCallsQueryOptions(executionId, listOpts),
    enabled: !!executionId && enabled,
    // Keep polling a finished run while any call's evals or CSAT are still
    // scoring — the same checks that make its cells load.
    refetchInterval: (query) => {
      const polled = query.state.data;
      if (ACTIVE_EXECUTION_STATUSES.has(polled?.execution?.status)) return 3000;
      if (STOPPED_RUN_STATUSES.has(polled?.execution?.status)) return false;
      const now = query.state.dataUpdatedAt || Date.now();
      return (polled?.results ?? []).some(
        (row) => isEvalScoring(row, now) || isCsatScoring(row, now),
      )
        ? 3000
        : false;
    },
  });

  const data = query.data;
  // Judge freshness as of each fetch: a poll that returns the same payload
  // keeps `data` identical, and a stuck scoring job must still stop loading.
  const fetchedAt = query.dataUpdatedAt;
  const { tasks, columns, count, groups, facets, summary, totalPages } =
    useMemo(() => {
      if (!data) {
        return {
          tasks: [],
          columns: [],
          count: 0,
          groups: [],
          facets: {},
          summary: null,
          totalPages: 1,
        };
      }
      const evalColumns = data.evaluation_columns ?? [];
      const mapOpts = {
        now: fetchedAt || Date.now(),
        runActive: ACTIVE_EXECUTION_STATUSES.has(data.execution?.status),
        runStopped: STOPPED_RUN_STATUSES.has(data.execution?.status),
      };
      const rows = (data.results ?? []).map((r) =>
        mapCallRow(r, evalColumns, mapOpts),
      );
      const rowsById = new Map(rows.map((row) => [row.id, row]));
      const serverGroups = (data.groups ?? []).map((group) => {
        const groupRows = (group.result_ids ?? [])
          .map((id) => rowsById.get(id))
          .filter(Boolean);
        const evals = Object.fromEntries(
          Object.entries(group.aggregates?.evaluations ?? {}).map(
            ([id, aggregate]) => [
              id,
              {
                scored: aggregate.scored,
                scoreSum: aggregate.score_sum,
              },
            ],
          ),
        );
        return {
          label: group.label,
          rows: groupRows,
          count: group.total,
          measured: group.measured,
          passed: group.outcomes?.passed ?? 0,
          agg: {
            csat: group.aggregates?.csat ?? null,
            turns: group.aggregates?.turns ?? null,
            latency: group.aggregates?.latency_ms ?? null,
            tokens: group.aggregates?.tokens ?? null,
            evals,
          },
        };
      });
      return {
        tasks: rows,
        columns: buildTraceColumns(evalColumns),
        count: data.count ?? rows.length,
        groups: serverGroups,
        facets: data.facets ?? {},
        summary: data.summary ?? null,
        totalPages: data.total_pages ?? 1,
      };
    }, [data, fetchedAt]);

  return {
    tasks,
    columns,
    count,
    groups,
    facets,
    summary,
    totalPages,
    isLoading: !!executionId && query.isPending,
    error: query.error,
  };
}
