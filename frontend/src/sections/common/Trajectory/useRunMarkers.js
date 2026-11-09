// Per-run marks for one row (R4 lane T): the same Task compare payload the
// side-by-side workspace reads (`useTaskCompare`), so `First wrong step`
// and the failing try are G0's on every screen.
import { useMemo } from "react";
import { useTaskCompare } from "src/api/develop/r3";
import { toLaneSteps } from "../Align/milestones";
import { marksOfLane } from "./trajectoryMarkers";

const FAILED = new Set(["fail", "error"]);

/** First failed try (1-based) of a lane's trials, or null. */
export const firstFailedTrial = (trials) =>
  (trials ?? []).find((t) => FAILED.has(t.outcome))?.trial ?? null;

/**
 * @param {{datasetId: string, rowId: string, runIds: string[]}} args
 * @param {{enabled?: boolean}} [opts]
 * @returns {{byRun: Object<string, {outcomes: {trial, outcome}[],
 *   firstFailed: number|null}>, marksOf: (runId, trial) => object|null,
 *   traceOf: (runId, trial) => string|null, isLoading: boolean}}
 */
export default function useRunMarkers(
  { datasetId, rowId, runIds },
  { enabled = true } = {},
) {
  const ids = (runIds ?? []).filter(Boolean);
  const q = useTaskCompare(
    { datasetId, rowId, runIds: ids },
    { enabled: enabled && ids.length > 0 },
  );
  return useMemo(() => {
    const lanes = q.data?.lanes ?? [];
    const laneOf = (id) => lanes.find((l) => l.run_id === id) ?? null;
    const trialOf = (id, t) =>
      laneOf(id)?.trials?.find((x) => x.trial === t) ?? null;
    const cache = new Map();
    const byRun = {};
    lanes.forEach((lane) => {
      byRun[lane.run_id] = {
        outcomes: (lane.trials ?? []).map((t) => ({
          trial: t.trial,
          outcome: t.outcome,
        })),
        firstFailed: firstFailedTrial(lane.trials),
      };
    });
    const marksOf = (id, t) => {
      const key = `${id}@${t}`;
      if (cache.has(key)) return cache.get(key);
      const lane = laneOf(id);
      const tr = trialOf(id, t);
      const m = tr
        ? marksOfLane({
            id,
            label: lane.label,
            steps: toLaneSteps(tr.steps ?? [], { runKind: lane.kind }),
            checks: tr.checks ?? [],
          })
        : null;
      cache.set(key, m);
      return m;
    };
    return {
      byRun,
      marksOf,
      traceOf: (id, t) => trialOf(id, t)?.trace_id ?? null,
      isLoading: q.isLoading,
    };
  }, [q.data, q.isLoading]);
}
