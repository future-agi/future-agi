import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { kpisQueryOptions } from "src/hooks/useKpis";
import { extractKpis } from "src/sections/test-detail/common";
import {
  RUNS_PAGE_SIZE,
  useEnvironmentRuns,
} from "src/api/simulate-environments/runs";
import { useAllRuns } from "src/api/simulate-environments/useAllRuns";
import { buildSummaryRow, deriveEvals } from "./summaryData";

// The run's per-eval scores, off the SAME kpis payload the run-detail page
// reads (`extractKpis(...).evalMetrics`), fetched once per run and cached, so
// the summary and the detail never disagree. `useKpis` cannot be called per
// run, so this spreads the same query options that hook does; only the
// `select` is this caller's own. A mock run (the `?mockRuns=1` switch) carries
// its scores inline, so no fetch is made for it. `enabled` false holds a
// run's fetch back until a caller needs it.
export function runScoreQuery(run, enabled = true) {
  return {
    ...kpisQueryOptions(run.executionId),
    enabled: enabled && !!run.executionId && !run.scores,
    select: (data) => extractKpis(data || {}, data?.agent_type).evalMetrics,
  };
}

// The runs table's data source: a page of the environment's runs as summary
// rows, newest first, plus the eval set its columns show. Real columns (pass
// rate, tasks, duration) come from the executions list row; per-eval scores
// from each run's kpis.
//
// The executions endpoint takes no agent version, so a `scope` (one agent
// version) pages the every-run list on the client instead, the same list the
// chart reads.
export function useRunsSummary(env, envState, paging, { scope = null } = {}) {
  const page = useEnvironmentRuns(env, envState, paging);
  const all = useAllRuns(env, envState);
  const { page: index = 0, pageSize = RUNS_PAGE_SIZE } = paging || {};

  const scoped = useMemo(
    () =>
      scope ? (all.runs || []).filter((r) => r.agentVersion === scope) : null,
    [all.runs, scope],
  );
  const runs = useMemo(
    () =>
      scoped
        ? scoped.slice(index * pageSize, (index + 1) * pageSize)
        : page.runs || [],
    [scoped, page.runs, index, pageSize],
  );
  const count = scoped ? scoped.length : page.count;
  const coveredScenarioCount = scoped ? null : page.coveredScenarioCount;
  const isLoading = scoped ? all.isLoading : page.isLoading;

  const scoreQueries = useQueries({
    queries: runs.map((r) => runScoreQuery(r)),
  });

  // A stable signal for the memo: the runs plus each run's resolved score map.
  const scoresById = Object.fromEntries(
    runs.map((r, i) => [r.id, r.scores || scoreQueries[i]?.data || {}]),
  );
  const scoreKey = JSON.stringify(scoresById);

  return useMemo(() => {
    const rows = runs.map((r) => buildSummaryRow(r, scoresById[r.id]));
    return {
      rows,
      evals: deriveEvals(rows),
      count,
      coveredScenarioCount,
      isLoading,
    };
    // scoreKey stands in for scoresById (fresh object each render); runs is stable
    // across renders while the query data is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, scoreKey, isLoading, count, coveredScenarioCount]);
}
