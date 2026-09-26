import { useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { KeyOptimizerMapping } from "src/sections/test-detail/CreateEditOptimization/common";

/**
 * The fix-my-agent (Debug-failures) DATA SOURCE.
 *
 * Two REAL product endpoints back this surface, so the ported designer flow
 * renders over genuine data rather than the prototype's fabricated optimizer
 * results:
 *
 *   - `testExecutions.getOptimizerAnalysis(id)` — the run diagnosis. Returns a
 *     model read of the run's failures split into agent / domain / system
 *     levels, each a list of actionable recommendations. This is the source of
 *     truth for the DiagnosisPane.
 *   - `optimizeSimulate.getOptimizationRuns()` — the list of past self-improvement
 *     (agent-prompt-optimiser) runs for this execution. Source of truth for the
 *     Trials tab's runs list.
 *
 * The mappers are pure and separately exported for unit tests. Fields the
 * endpoints do NOT populate (projected lift %, prompt diffs, held-out scores,
 * trial heatmaps) are deliberately absent from these view-models — the panes
 * that would have shown them are gated behind ComingSoon rather than fed a
 * fabricated number.
 */

// Priority ordering for the recommendation list: a high-priority finding sorts
// above a medium one; within a priority the one touching more calls leads. The
// endpoint returns "high" | "medium" | "low" (see the product FixMyAgent
// consumer); anything unrecognised sorts last.
const PRIORITY_RANK = { high: 0, medium: 1, low: 2 };

/**
 * @typedef {Object} DiagnosisRecommendation
 * @property {string} id             Stable key (`level:index`).
 * @property {string} heading        Short title.
 * @property {string} recommendation The suggested change (body).
 * @property {string[]} breakingPoints  Concrete failure points ("View issue").
 * @property {"high"|"medium"|"low"|null} priority
 * @property {string[]} callExecutionIds  Calls this finding addresses.
 * @property {number} callsAffected  `callExecutionIds.length`.
 * @property {?string} branchCategory Branch label, or null / "Unknown".
 * @property {"agent"|"domain"|"system"} level  Which analyzer level produced it.
 */

/**
 * @typedef {Object} OptimizerAnalysis
 * @property {?string} status        pending | running | completed | failed.
 * @property {boolean} isWorking     status is pending/running (poll + spinner).
 * @property {boolean} hasResponse   Whether a diagnosis body exists yet.
 * @property {?string} summary       `response.insights` — the run-level summary.
 * @property {?string} humanComparison `system_level.human_comparison_summary`.
 * @property {?string} lastUpdated   ISO string, when the diagnosis was produced.
 * @property {DiagnosisRecommendation[]} fixable        agent + domain level.
 * @property {DiagnosisRecommendation[]} environmental  system level (belongs to
 *                                       the environment/measurement, not the agent).
 */

// The statuses that mean the diagnosis is still being produced — poll while in
// one of them, exactly like the product's `FixMyAgentRefetchStates`.
export const ANALYSIS_WORKING_STATES = ["pending", "running"];

function mapRecommendation(raw, level, index) {
  const callExecutionIds = Array.isArray(raw?.call_execution_ids)
    ? raw.call_execution_ids
    : [];
  const branch = raw?.branch_category;
  return {
    id: `${level}:${index}`,
    heading: raw?.heading ?? "",
    recommendation: raw?.recommendation ?? "",
    breakingPoints: Array.isArray(raw?.breaking_points) ? raw.breaking_points : [],
    priority: raw?.priority ?? null,
    callExecutionIds,
    callsAffected: callExecutionIds.length,
    branchCategory: branch && branch !== "Unknown" ? branch : null,
    level,
  };
}

function sortByPriority(recs) {
  return [...recs].sort((a, b) => {
    const pa = PRIORITY_RANK[a.priority] ?? 99;
    const pb = PRIORITY_RANK[b.priority] ?? 99;
    if (pa !== pb) return pa - pb;
    return b.callsAffected - a.callsAffected;
  });
}

/**
 * Maps one raw `optimiser-analysis` payload (the `result` object the endpoint
 * wraps) → an `OptimizerAnalysis`. Pure and exported for unit tests.
 * @param {?Object} result  `res.data.result`.
 * @returns {OptimizerAnalysis}
 */
export function mapOptimizerAnalysis(result) {
  const response = result?.response ?? null;
  const level = (key) =>
    (Array.isArray(response?.[key]?.actionable_recommendations)
      ? response[key].actionable_recommendations
      : []
    ).map((r, i) => mapRecommendation(r, key.replace("_level", ""), i));

  const agent = level("agent_level");
  const domain = level("domain_level");
  const system = level("system_level");
  const status = result?.status ?? null;

  return {
    status,
    isWorking: ANALYSIS_WORKING_STATES.includes(status),
    hasResponse: Boolean(response),
    summary: response?.insights ?? null,
    humanComparison: response?.system_level?.human_comparison_summary ?? null,
    lastUpdated: result?.last_updated ?? result?.lastUpdated ?? null,
    fixable: sortByPriority([...agent, ...domain]),
    environmental: system,
  };
}

/**
 * The run-diagnosis hook. Reads `optimiser-analysis`, polls at 5s while the
 * diagnosis is still being produced, and exposes a `refresh` that re-triggers
 * the analysis (POST `…/refresh/`) then refetches — the designer's "read the
 * run again" affordance.
 * @param {?string} executionId
 * @returns {{ analysis: OptimizerAnalysis, isLoading: boolean,
 *   refresh: Function, isRefreshing: boolean }}
 */
export function useOptimizerAnalysis(executionId) {
  const query = useQuery({
    queryKey: ["optimizer-analysis", executionId],
    queryFn: () =>
      axios
        .get(endpoints.testExecutions.getOptimizerAnalysis(executionId))
        .then((res) => res.data?.result),
    enabled: !!executionId,
    refetchInterval: ({ state }) =>
      ANALYSIS_WORKING_STATES.includes(state?.data?.status) ? 5000 : false,
    refetchOnWindowFocus: false,
  });

  const refreshMutation = useMutation({
    mutationFn: () =>
      axios.post(endpoints.testExecutions.refreshOptimizerAnalysis(executionId)),
    onSuccess: () => query.refetch(),
  });

  const analysis = useMemo(
    () => mapOptimizerAnalysis(query.data),
    [query.data],
  );

  return {
    analysis,
    isLoading: !!executionId && query.isPending,
    refresh: refreshMutation.mutate,
    isRefreshing:
      refreshMutation.isPending || analysis.isWorking || query.isRefetching,
  };
}

/**
 * @typedef {Object} OptimizationRunRow
 * @property {string} id           Optimisation id (routes to its detail).
 * @property {string} name         `optimisation_name`.
 * @property {?string} startedAt   `started_at` ISO.
 * @property {number} trials       `no_of_trials`.
 * @property {string} optimiserType  Raw `optimiser_type` key.
 * @property {string} optimiserLabel Human label (`KeyOptimizerMapping`), or the
 *                                   raw key if unmapped.
 * @property {?string} status      pending | running | completed | failed.
 */

/**
 * Maps the real `getOptimizationRuns` list payload → runs-list rows. Pure.
 * The endpoint returns `{ result: { table: [...], metadata: { total_rows } } }`.
 * @param {?Object} raw  `res.data`.
 * @returns {OptimizationRunRow[]}
 */
export function mapOptimizationRuns(raw) {
  const table = Array.isArray(raw?.result?.table) ? raw.result.table : [];
  return table.map((row) => ({
    id: row?.id,
    name: row?.optimisation_name ?? "Optimization",
    startedAt: row?.started_at ?? null,
    trials: row?.no_of_trials ?? 0,
    optimiserType: row?.optimiser_type ?? null,
    optimiserLabel:
      KeyOptimizerMapping[row?.optimiser_type] ?? row?.optimiser_type ?? "-",
    status: row?.status ?? null,
  }));
}

/**
 * The past-optimizations (Trials tab) list hook, scoped to one execution.
 * @param {?string} executionId
 * @returns {{ runs: OptimizationRunRow[], isLoading: boolean }}
 */
export function useOptimizationRuns(executionId) {
  const query = useQuery({
    queryKey: ["agent-optimization-runs", executionId],
    queryFn: () =>
      axios
        .get(endpoints.optimizeSimulate.getOptimizationRuns(), {
          params: { test_execution_id: executionId, page: 1, page_size: 50 },
        })
        .then((res) => res.data),
    enabled: !!executionId,
    staleTime: 1000 * 30,
  });

  const runs = useMemo(() => mapOptimizationRuns(query.data), [query.data]);
  return { runs, isLoading: !!executionId && query.isPending };
}
