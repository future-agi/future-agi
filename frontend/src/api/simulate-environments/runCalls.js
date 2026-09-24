import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
import { TRACE_COLUMNS } from "src/sections/simulate/environments/workspace/runs/detail/trace/traceTable.constants";
import { ACTIVE_EXECUTION_STATUSES } from "src/sections/simulate/environments/workspace/runs/runs.constants";

/** Adapt the v3 run-results contract for the trace table. */

const to01 = (n) => (n == null ? null : n <= 1 ? n : n / 100);

// One cell from a live `evaluations[]` entry — the array shape the isolated
// v3 run-results contract sends, where the server has already scored the eval.
function liveEvalCell(col, data) {
  return {
    id: col.id,
    name: data.name || col.name || col.column_name || col.id,
    score: data.score ?? null,
    passed: data.passed ?? null,
    label: typeof data.value === "string" ? data.value : null,
    reason: data.reason || "",
    threshold: 0.5,
    removed: data.removed === true,
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

/**
 * Maps one raw call row → a `RunTask`. Pure.
 * @param {Object} row          A `results[]` row from `testExecutions.list`.
 * @param {Array} evalColumns   The `column_order` entries of type "evaluation".
 * @returns {import("./runDetail").RunTask}
 */
export function mapCallRow(row, evalColumns = []) {
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
 * The per-call table hook. Reads the product's real executions list and adapts
 * it to `{ tasks, columns, count }`. `opts` mirror the product grid's params.
 * @param {string} executionId
 * @param {{ page?: number, limit?: number, search?: string, filters?: Object }} [opts]
 * @returns {{ tasks: import("./runDetail").RunTask[],
 *   columns: import("./runDetail").TraceColumn[], count: number,
 *   isLoading: boolean }}
 */
export function useRunCalls(executionId, opts = {}) {
  const {
    page = 1,
    limit = 100,
    search = "",
    filters = {},
    groupBy = "goal",
  } = opts;
  const query = useQuery({
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
    enabled: !!executionId,
    refetchInterval: (query) =>
      ACTIVE_EXECUTION_STATUSES.has(query.state.data?.execution?.status)
        ? 3000
        : false,
    staleTime: 1000 * 60,
  });

  const data = query.data;
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
      const rows = (data.results ?? []).map((r) => mapCallRow(r, evalColumns));
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
    }, [data]);

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
