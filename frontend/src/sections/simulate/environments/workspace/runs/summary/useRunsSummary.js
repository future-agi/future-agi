import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { kpisQueryOptions } from "src/hooks/useKpis";
import { extractKpis } from "src/sections/test-detail/common";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import { buildSummaryRow, buildEvalSeries, deriveEvals } from "./summaryData";

// The populated Runs tab's data source: every run of the environment as one
// summary — the table rows, the derived eval set, and the per-eval trend series
// the graph draws.
//
// Real columns (pass rate, tasks, avg duration) come from the executions list
// row. Per-eval scores come from each run's kpis — the SAME payload the
// run-detail page reads (`extractKpis(...).evalMetrics`), fetched once per run
// and cached — so the summary and the detail never disagree. A mock run (the
// `?mockRuns=1` switch) carries its scores inline, so no fetch is made for it.
export function useRunsSummary(env, envState) {
  const { runs, isLoading: runsLoading } = useEnvironmentRuns(env, envState);

  const scoreQueries = useQueries({
    // `useKpis` cannot be called here (one query per run, count unknown), so
    // this spreads the same query options that hook does rather than
    // restating its key, fetch and freshness — a second copy is what let the
    // two observers of this key cache different shapes. Only the `select` is
    // this caller's own.
    queries: runs.map((r) => ({
      ...kpisQueryOptions(r.executionId),
      // Skip the fetch when the run already carries its scores (a mock run).
      enabled: !!r.executionId && !r.scores,
      select: (data) => extractKpis(data || {}, data?.agent_type).evalMetrics,
    })),
  });

  // A stable signal for the memo: the runs plus each run's resolved score map.
  const scoreData = runs.map((r, i) => r.scores || scoreQueries[i]?.data || {});
  const scoreKey = JSON.stringify(scoreData);

  return useMemo(() => {
    // Rows are newest-first (the executions list is already sorted that way) for
    // the table; the graph reads them oldest-first so the trend runs left→right.
    const rows = runs.map((r, i) => buildSummaryRow(r, scoreData[i]));
    const rowsChrono = [...rows].reverse();
    const evals = deriveEvals(rows);
    const series = buildEvalSeries(rowsChrono, evals);
    return { rows, rowsChrono, evals, series, isLoading: runsLoading };
    // scoreKey stands in for scoreData (fresh array each render); runs is stable
    // across renders while the query data is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, scoreKey, runsLoading]);
}
