/*
  A finished run read the way its own page reads it: rebuilt from the
  environment and re-graded with any grader fix it was re-graded with. The
  Runs list, its chart and the run page then show the same score.
*/
import { rebuildRun } from "./comparison";
import { withRegrade } from "./regrade";
import { runStatsOf } from "./runCompare";

const cache = new WeakMap();

export const gradedRunOf = (env, envState, run) => {
  if (!run?.finishedAt || !env || !envState || run.kind === "trial") return run;
  const hit = cache.get(run);
  if (hit && hit.envState === envState) return hit.value;
  let tasks = null;
  try {
    tasks = withRegrade(rebuildRun(env, envState, run), run);
  } catch (e) {
    tasks = null;
  }
  if (!tasks?.length) {
    cache.set(run, { envState, value: run });
    return run;
  }
  /* The stored pass rate is from before any re-grade: count it again. */
  const callRate = runStatsOf({ ...run, tasks }).callRate;
  const value = {
    ...run,
    tasks,
    ...(callRate != null && { passRate: Math.round(callRate) }),
  };
  cache.set(run, { envState, value });
  return value;
};
