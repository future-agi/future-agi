import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { useAllRuns } from "src/api/simulate-environments/useAllRuns";
import { buildSummaryRow, deriveEvals, isFinishedRun } from "./summaryData";
import { runScoreQuery } from "./useRunsSummary";

// The top of the Runs tab: every finished run (up to the cap), oldest first,
// as summary rows, the latest and the run before it, and the evals to offer:
// those two runs' plus the runs table's (`tableRows`), so an eval the table
// shows can be drawn even when the latest runs weren't scored on it.
//
// `scope` keeps one agent version's runs. `baselineId` is looked up across
// every run, in scope or not, since a baseline can be on another version.
//
// Scores are a kpis fetch per run, so only the latest two and the baseline
// are fetched up front (the chips and the deltas read them). The rest load
// once an eval is drawn on the chart (`focusId`) or `scoreAll` asks (Choose
// winner ranks on every run's scores).
export function useRunsOverview(
  env,
  envState,
  focusId,
  tableRows = [],
  { scope = null, scoreAll = false, baselineId = null } = {},
) {
  const { runs, capped, isLoading } = useAllRuns(env, envState);
  const finishedAll = useMemo(
    () => runs.filter(isFinishedRun).reverse(),
    [runs],
  );
  const inScope = (r) => !scope || r.agentVersion === scope;
  const finished = finishedAll.filter(inScope);
  const lastTwo = finished.slice(-2).map((r) => r.id);

  const scoreQueries = useQueries({
    queries: finishedAll.map((r) =>
      runScoreQuery(
        r,
        scoreAll ||
          (!!focusId && inScope(r)) ||
          lastTwo.includes(r.id) ||
          r.id === baselineId,
      ),
    ),
  });
  const scoresById = Object.fromEntries(
    finishedAll.map((r, i) => [r.id, r.scores || scoreQueries[i]?.data || {}]),
  );
  const scoreKey = JSON.stringify(scoresById);

  return useMemo(() => {
    const allRows = finishedAll.map((r) =>
      buildSummaryRow(r, scoresById[r.id]),
    );
    const rows = allRows.filter(inScope);
    const latest = rows[rows.length - 1] || null;
    const previous = rows[rows.length - 2] || null;
    return {
      rows,
      latest,
      previous,
      evals: deriveEvals([latest, previous, ...tableRows].filter(Boolean)),
      baseline: allRows.find((r) => r.id === baselineId) || null,
      runs,
      capped,
      isLoading,
    };
    // scoreKey stands in for scoresById (a fresh object each render); scope
    // stands in for inScope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    finishedAll,
    scoreKey,
    scope,
    baselineId,
    runs,
    capped,
    isLoading,
    tableRows,
  ]);
}
