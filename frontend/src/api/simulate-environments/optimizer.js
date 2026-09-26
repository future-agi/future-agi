import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { KeyOptimizerMapping } from "src/sections/test-detail/CreateEditOptimization/common";

/**
 * The past self-improvement (agent-prompt-optimiser) runs for one execution —
 * the Trials tab's list, over the REAL `optimizeSimulate.getOptimizationRuns()`.
 * The run diagnosis lives in `debugAnalysis.js`.
 */

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
      KeyOptimizerMapping[row?.optimiser_type] ?? row?.optimiser_type ?? "—",
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
