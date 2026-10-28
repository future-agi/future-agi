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
// Scores are a kpis fetch per run, so only the latest two are fetched up
// front (the chips read them). The rest load once an eval is drawn on the
// chart (`focusId`), since its line needs a score from every run.
export function useRunsOverview(env, envState, focusId, tableRows = []) {
  const { runs, capped, isLoading } = useAllRuns(env, envState);
  const finished = useMemo(() => runs.filter(isFinishedRun).reverse(), [runs]);
  const lastTwo = finished.slice(-2).map((r) => r.id);

  const scoreQueries = useQueries({
    queries: finished.map((r) =>
      runScoreQuery(r, !!focusId || lastTwo.includes(r.id)),
    ),
  });
  const scoresById = Object.fromEntries(
    finished.map((r, i) => [r.id, r.scores || scoreQueries[i]?.data || {}]),
  );
  const scoreKey = JSON.stringify(scoresById);

  return useMemo(() => {
    const rows = finished.map((r) => buildSummaryRow(r, scoresById[r.id]));
    const latest = rows[rows.length - 1] || null;
    const previous = rows[rows.length - 2] || null;
    return {
      rows,
      latest,
      previous,
      evals: deriveEvals([latest, previous, ...tableRows].filter(Boolean)),
      capped,
      isLoading,
    };
    // scoreKey stands in for scoresById (a fresh object each render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished, scoreKey, capped, isLoading, tableRows]);
}
