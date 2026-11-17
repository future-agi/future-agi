// HO results R7 (W5 B-H2, the leaderboard half): the one read the
// leaderboard's columns take run metrics from (columnCatalog `runMetrics`).
// It asks once the board's runs are known (never a definitions-only read
// first, whose catalog entry would leave again when the runs' read starts),
// asks again when a run's own numbers move (a running run scores more
// rows), and keeps its last answer meanwhile, so the column never drops out
// and each cell is the server's value for the run as the board shows it.
import { useMemo } from "react";
import { useDerivedMetrics } from "./useDerivedMetrics";
import { runsStamp } from "./leaderboardRunMetrics";

/**
 * @param {string} datasetId
 * @param {object[]} runs the board's runs (R1 results)
 * @returns {{metrics: object[], values: object, errors?: object}|undefined}
 */
export function useLeaderboardRunMetrics(datasetId, runs) {
  const runIds = useMemo(
    () => (runs ?? []).map((r) => r.run_id).filter(Boolean),
    [runs],
  );
  const stamp = runsStamp(runs);
  return useDerivedMetrics(datasetId, runIds, {
    enabled: runIds.length > 0,
    keepPrevious: true,
    stamp,
  }).data;
}

export default useLeaderboardRunMetrics;
