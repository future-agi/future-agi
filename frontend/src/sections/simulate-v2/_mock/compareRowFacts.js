/*
  What one run's cell of a compare row says, as plain numbers, and how each
  moved against the baseline's cell. Kept out of the component so the rules
  (absolute points, same arrow everywhere) are testable.
*/
import { isMeaningful, readDelta, toPoints } from "./scoreDelta";

/** [{ id, pts, passed }] for the evals asked about; pts null when not scored. */
export const evalPointsOf = (task, evals) =>
  evals.map((e) => {
    const r = (task?.evalResults || []).find((x) => x.id === e.id);
    return {
      id: e.id,
      pts: r ? toPoints(r.score) : null,
      passed: r ? !!r.passed : null,
    };
  });

/** "5 of 6 met" as { met, total } from a sub-goal result list; null when it has none. */
export const subGoalCountOf = (items) => {
  if (!Array.isArray(items) || !items.length) return null;
  return {
    met: items.filter((g) => g.status === "met").length,
    total: items.length,
  };
};

/** A delta tagged with whether it is a real change (kind: "score" | "count"). */
const marked = (d, kind) =>
  d ? { ...d, meaningful: isMeaningful(d, kind) } : d;

/** How many times a cell repeated its scenario; null when unknown. */
export const trialCountOf = (cell) =>
  cell?.repeats ?? cell?.task?.samples?.length ?? null;

/**
 * Facts for one cell and its deltas against the baseline cell.
 * `subGoalsOf(task)` is injected so this stays free of the mock store.
 */
export const cellFactsOf = (cell, baseCell, evals, subGoalsOf = () => null) => {
  const task = cell?.task || null;
  const baseTask = baseCell?.task || null;
  const isBase = !baseCell || cell === baseCell;
  const measured =
    !!task && cell.measured !== false && cell.status !== "missing";
  const baseMeasured =
    !!baseTask && baseCell.measured !== false && baseCell.status !== "missing";
  const compare = !isBase && measured && baseMeasured;
  const ev = evalPointsOf(task, evals);
  const baseEv = evalPointsOf(baseTask, evals);
  const sg = measured ? subGoalsOf(task) : null;
  const baseSg = baseMeasured ? subGoalsOf(baseTask) : null;
  const n = trialCountOf(cell);
  const baseN = trialCountOf(baseCell);
  // "Passed every trial" is stricter with more trials: no like-for-like change.
  const trialsDiffer = compare && n != null && baseN != null && n !== baseN;
  return {
    measured,
    trialsNote: trialsDiffer ? { n, baseN } : null,
    evals: ev.map((e, i) => ({
      ...e,
      delta: compare ? marked(readDelta(e.pts, baseEv[i].pts), "score") : null,
    })),
    subGoals: sg,
    subGoalsDelta:
      compare && sg && baseSg
        ? marked(readDelta(sg.met, baseSg.met), "count")
        : null,
    passesDelta:
      compare && !trialsDiffer && cell.passes != null && baseCell.passes != null
        ? marked(readDelta(cell.passes, baseCell.passes), "count")
        : null,
  };
};
