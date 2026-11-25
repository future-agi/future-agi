/*
  Reading runs against each other — the numbers the Runs tab leads with.

  A run's tasks are one per scenario; each carries `passShare`, the share of
  its repeats that passed. So:
    scenario pass — scenarios that passed every repeat (a flaky one is not a
                    pass — the same rule as the run header and the Flaky chip)
    call pass     — every repeat counted (the mean share; pass^1)
  Two runs are compared only on the scenarios both measured, and every change
  comes with the noise two samples of that size carry (95%, binomial), so a
  swing inside it isn't read as a regression or a fix.
*/

import { runRollupOf } from "./runRollup";
import { VERDICT_CAUSES } from "./decoupled";

const keyOf = (t) => t.scenarioId || t.id;
const shareOf = (t) => t.passShare ?? (t.status === "passed" ? 1 : 0);
/* A verdict for or against the agent — not inconclusive, invalid or errored. */
/* An improvement's run carries its outcomes as `trialTasks` (comparison.js). */
const tasksOf = (run) => (run?.tasks?.length ? run.tasks : run?.trialTasks || []);
const measuredTasks = (run) => tasksOf(run).filter((t) => !t.pulled && ["passed", "failed", "flaky"].includes(t.status));

/** The columns a run row shows. */
export const runStatsOf = (run) => {
  const m = measuredTasks(run);
  const passed = m.filter((t) => shareOf(t) >= 1).length;
  const repeats = run?.repeats || 1;
  const took = run?.startedAt && run?.finishedAt ? Date.parse(run.finishedAt) - Date.parse(run.startedAt) : null;
  return {
    measured: m.length,
    scenarioPassed: passed,
    scenarioRate: m.length ? Math.round((passed / m.length) * 100) : null,
    callRate: m.length ? Math.round((m.reduce((a, t) => a + shareOf(t), 0) / m.length) * 1000) / 10 : null,
    calls: tasksOf(run).length * repeats,
    flaky: m.filter((t) => t.status === "flaky").length,
    /* Flaky has its own column, so Failing is only the ones that failed. */
    failed: m.length - passed - m.filter((t) => t.status === "flaky").length,
    tookMs: took && took > 0 ? took : null,
  };
};

/*
  The Runs table's scenario pass, read exactly as the run's own page reads it
  (runRollupOf: scored = in scope and graded; passed = every trial passed), so
  a run never shows 57% on its page and 65% in the list.
*/
export const alignedStatsOf = (run, ctx = {}) => {
  const base = runStatsOf(run);
  if (!run?.finishedAt || !ctx.envState) return base;
  const s = runRollupOf({ ...run, tasks: tasksOf(run) }, ctx);
  if (!s || !s.scored) return base;
  return {
    ...base,
    measured: s.scored,
    scenarioPassed: s.passedAll,
    scenarioRate: Math.round((s.passedAll / s.scored) * 100),
    failed: Math.max(0, s.scored - s.passedAll - base.flaky),
  };
};

/*
  The run as its chip reads it: an improvement's run is its own per-scenario
  outcomes (trialTasks), even once Compare has hydrated it with the source
  run's calls.
*/
export const chipRunOf = (run) =>
  run?.kind === "trial" && run.trialTasks?.length ? { ...run, tasks: run.trialTasks } : run;

/** Chip numbers for a run — alignedStatsOf on the run as its chip reads it. */
export const chipStatsOf = (run, ctx = {}) => alignedStatsOf(chipRunOf(run), ctx);

/**
 * The scenarios a run scored (id -> pass share), read the way its own page
 * reads them (runRollupOf: in scope and graded). null when the run can't be
 * rolled up, so callers fall back to their own measure.
 */
export const scoredSharesOf = (run, ctx = {}) => {
  const r = chipRunOf(run);
  if (!r?.finishedAt || !ctx.envState) return null;
  const tasks = tasksOf(r);
  const s = runRollupOf({ ...r, tasks }, ctx);
  if (!s?.verdicts) return null;
  const out = new Map();
  tasks.forEach((t) => {
    const v = s.verdicts.get(t.id);
    if (!v || v.pulled || v.stale || v.exploration) return;
    if (!VERDICT_CAUSES[v.cause]?.scored) return;
    out.set(t.id, shareOf(t));
  });
  return out;
};

const noiseOf = (p1, n1, p2, n2) => Math.max(1, Math.round(1.96 * Math.sqrt((p1 * (1 - p1)) / n1 + (p2 * (1 - p2)) / n2) * 100));

/**
 * `run` against `base`, on the scenarios both measured:
 * { pts, noise, within, shared } — null when they share fewer than 3.
 */
export const sharedDeltaOf = (run, base) => {
  if (!run || !base) return null;
  const a = new Map(measuredTasks(run).map((t) => [keyOf(t), shareOf(t) >= 1]));
  const b = new Map(measuredTasks(base).map((t) => [keyOf(t), shareOf(t) >= 1]));
  const shared = [...a.keys()].filter((k) => b.has(k));
  if (shared.length < 3) return null;
  const p1 = shared.filter((k) => a.get(k)).length / shared.length;
  const p2 = shared.filter((k) => b.get(k)).length / shared.length;
  const pts = Math.round((p1 - p2) * 100);
  const noise = noiseOf(p1, shared.length, p2, shared.length);
  return { pts, noise, within: Math.abs(pts) <= noise, shared: shared.length };
};

/*
  Runs are read against each other only on the same environment version —
  the one people see. Callers that know the environment pass `versionOf`
  (envSnapshot.snapshotLabelOf); without it the stamped version is used.
*/
const stampedVersion = (r) => r?.envVersion || null;

/** Whether two runs sit on the same environment version. */
export const sameEnvVersion = (a, b, versionOf = stampedVersion) => {
  if (!a || !b) return false;
  const va = versionOf(a);
  const vb = versionOf(b);
  /* Neither stamped (runs from before versions existed): one version. */
  return va === vb || (!va && !vb);
};

/**
 * The run a run is read against: the latest earlier one on the same
 * environment version. None there, none at all — a run on another version
 * is never the comparison.
 */
export const previousRunOf = (runs, run, versionOf = stampedVersion) => {
  if (!run) return null;
  const earlier = runs.filter((r) => r.id !== run.id && (r.ordinal || 0) < (run.ordinal || 0) && r.finishedAt && measuredTasks(r).length);
  const same = earlier.filter((r) => sameEnvVersion(r, run, versionOf));
  return [...same].sort((x, y) => (y.ordinal || 0) - (x.ordinal || 0))[0] || null;
};

/** Plain words for a delta: "+4 pts ±19 · within noise". */
export const deltaWords = (d) => (d
  ? `${d.pts > 0 ? "+" : ""}${d.pts} pts ±${d.noise} · ${d.within ? "within noise" : d.pts > 0 ? "better" : "worse"}`
  : null);

/* Runs smaller than this are left off the trend — one point from four
   scenarios beside one from forty invites the wrong read. */
const MIN_FOR_TREND = 5;

/**
 * The trend line: scenario pass rate per run, on the scenarios every charted
 * run measured (so the line moves with the agent, not with the suite). Falls
 * back to each run's own scenarios when they share too few.
 *   { points: [{ run, rate, failing }], shared: n | null, left: [run] }
 */
export const trendOf = (runs) => {
  const charted = runs.filter((r) => r.finishedAt && measuredTasks(r).length >= MIN_FOR_TREND);
  const left = runs.filter((r) => r.finishedAt && !charted.includes(r));
  if (!charted.length) return { points: [], shared: null, left };
  const sets = charted.map((r) => new Set(measuredTasks(r).map(keyOf)));
  const common = [...sets[0]].filter((k) => sets.every((s) => s.has(k)));
  const useShared = common.length >= MIN_FOR_TREND;
  const points = charted.map((r) => {
    const tasks = measuredTasks(r).filter((t) => !useShared || common.includes(keyOf(t)));
    const passed = tasks.filter((t) => shareOf(t) > 0.5).length;
    return { run: r, rate: tasks.length ? Math.round((passed / tasks.length) * 100) : 0, failing: tasks.length - passed, of: tasks.length };
  });
  return { points, shared: useShared ? common.length : null, left };
};
