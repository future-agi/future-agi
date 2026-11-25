/*
  Seeded examples for the scenarios table's "Last 3 runs" column, so every
  case can be seen without setting anything up. Idempotent — applied on every
  load, a no-op once the environment carries the added scenario:
    · one scenario added after the runs (never run — nothing in the column)
  Flaky, failed and passed scenarios come from the seeded runs themselves. No
  scenario is bumped to a newer version here: the environment, its runs and
  the compare page all say scenarios v1, so a hollow dot would contradict
  them. (The hollow-dot feature itself is covered by the unit tests.)
*/

import { runSummaries } from "./comparison";

const DEMO_ENVS = ["env-voice-support", "env-returns-line"];
const NEVER_RAN_ID = "scn-never-ran-demo";

const withDemoIn = (env, es) => {
  const rows = es?.scenarios || [];
  if (!rows.length) return es;
  if (
    rows.some(
      (r) => r.id === NEVER_RAN_ID || (r.version && r.version !== "1.0.0"),
    )
  )
    return es;
  const runs = runSummaries(env, es).filter(
    (r) => r.finishedAt && !r.synthetic,
  );
  if (!runs.length) return es;
  const live = rows.filter((r) => !r.archived && !r.retired);
  if (!live.length) return es;
  const base = live.find((r) => !r.proposed) || live[0];
  const added = {
    ...base,
    id: NEVER_RAN_ID,
    name: NEVER_RAN_ID,
    title: "Caller hangs up halfway through the verification step",
    summary: "Caller hangs up halfway through the verification step",
    version: "1.0.0",
    versionLog: undefined,
    critical: false,
    archived: false,
  };
  delete added.versionLog;
  return { ...es, scenarios: [...rows, added] };
};

export const withScenarioHistoryDemo = (state) => {
  if (!state?.byEnv) return state;
  let changed = false;
  const byEnv = { ...state.byEnv };
  DEMO_ENVS.forEach((id) => {
    const env = (state.myEnvironments || []).find((e) => e.id === id);
    const es = byEnv[id];
    if (!env || !es) return;
    const out = withDemoIn(env, es);
    if (out !== es) {
      byEnv[id] = out;
      changed = true;
    }
  });
  return changed ? { ...state, byEnv } : state;
};
