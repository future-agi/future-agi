/*
  What a full Run takes — one count for every place that says it (the Run
  button and its tooltip, Repeats, the evals cost, the first-run offer).

  The plan's scenarios (what can stage on the version in use), minus the ones
  resting on an open or skipped optional question in Needs input: Run anyway
  leaves those out. A scenario on such a question that can't stage anyway
  doesn't count as sitting out — it was never in the run.
*/
import { runPlan } from "./proofs";
import { gapCoverage, setupGaps } from "./setupGaps";
import { staleScenariosOf } from "./runCauses";

/**
 * { plan, runnable, ids, count, sitOut, sitOutGaps }
 *   runnable   — scenarios that can stage
 *   ids/count  — the ones a full Run takes
 *   sitOut     — runnable − count
 *   sitOutGaps — how many open questions those wait on
 * Pass `plan` / `gaps` when the caller already has them.
 */
export const runScopeOf = (env, envState, { plan = null, gaps = null } = {}) => {
  const p = plan || runPlan(env, envState);
  const all = gaps || setupGaps(env, envState);
  const cover = gapCoverage(env, envState, all);
  const run = p?.run || [];
  const ids = run.filter((sc) => !cover.attribution[sc.id]).map((sc) => sc.id);
  const sitOutIds = new Set(run.map((sc) => sc.id).filter((id) => cover.attribution[id]));
  const sitOutGaps = all.filter((g) => (g.status === "optional" || g.status === "skipped")
    && (g.coverage || []).some((id) => sitOutIds.has(id))).length;
  return { plan: p, runnable: run.length, ids, count: ids.length, sitOut: sitOutIds.size, sitOutGaps };
};

const top = (list) => {
  const n = {};
  list.forEach((x) => { n[x] = (n[x] || 0) + 1; });
  return Object.entries(n).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
};

/**
 * What the run plan counts before it resolves IDs (roadmap runs.md "Scenario
 * scope"): eligible — what runs; stale — runs, counts toward no decision;
 * blocked — can't stage on this version; excluded — archived, sitting out on
 * an open question, or outside the selection. Each with its main reason.
 */
export const scopeBreakdownOf = (env, envState, { plan = null, gaps = null, only = null, agentLabel = null } = {}) => {
  const s = runScopeOf(env, envState, { plan, gaps });
  const wanted = only?.length ? new Set(only) : null;
  const runRows = wanted ? s.plan.run.filter((sc) => wanted.has(sc.id)) : s.plan.run.filter((sc) => s.ids.includes(sc.id));
  const blocked = (s.plan.skipped || []).filter((sc) => !wanted || wanted.has(sc.id));
  const shelved = (envState?.scenarios || []).filter((sc) => (sc.archived || sc.retired) && !sc.proposed && (!wanted || wanted.has(sc.id))).length;
  const unselected = wanted ? Math.max(0, s.plan.poolSize - runRows.length - blocked.length) : 0;
  const sitOut = wanted ? 0 : s.sitOut;
  const stale = Object.keys(staleScenariosOf(env, envState, runRows, agentLabel) || {}).length;
  return {
    eligible: runRows.length,
    stale,
    blocked: blocked.length,
    blockedWhy: top(blocked.map((sc) => String(sc.skipReason || "").split(" — ")[0])),
    excluded: shelved + sitOut + unselected,
    excludedWhy: [
      unselected && `${unselected} outside the selection`,
      sitOut && `${sitOut} waiting on ${s.sitOutGaps === 1 ? "an open question" : `${s.sitOutGaps} open questions`}`,
      shelved && `${shelved} archived`,
    ].filter(Boolean).join(", "),
  };
};
