// Dataset Summary · Evals when the dataset has no evals of its own but its
// experiments scored their runs (R3 analysis fix): the latest experiment's
// runs, its baseline and a per-eval pass rate per run. Pure.

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const TERMINAL = new Set(["Completed", "PartialCompleted"]);

/**
 * The experiment the Evals tab reads: the most recent one with a completed
 * run. Returns `{experimentId, experimentName, runIds, baselineId}` (runs in
 * letter order; baseline = the pinned dataset run when it is one of them,
 * else run A) or null.
 * @param {object[]} runs  R1 `results`
 * @param {string|null} pinnedRunId
 */
export function latestExperiment(runs, pinnedRunId) {
  const byExp = new Map();
  (runs ?? []).forEach((r) => {
    if (!r?.experiment_id) return;
    const list = byExp.get(r.experiment_id) ?? [];
    list.push(r);
    byExp.set(r.experiment_id, list);
  });
  let best = null;
  byExp.forEach((list, id) => {
    if (!list.some((r) => TERMINAL.has(r.status))) return;
    const latest = list.reduce(
      (m, r) => (String(r.created_at) > m ? String(r.created_at) : m),
      "",
    );
    if (!best || latest > best.latest) best = { id, list, latest };
  });
  if (!best) return null;
  const ordered = [...best.list].sort((a, b) =>
    String(a.letter ?? a.label).localeCompare(String(b.letter ?? b.label)),
  );
  const runIds = ordered.map((r) => r.run_id);
  return {
    experimentId: best.id,
    experimentName: ordered[0]?.experiment_name ?? null,
    runIds,
    baselineId:
      pinnedRunId && runIds.includes(pinnedRunId) ? pinnedRunId : runIds[0],
  };
}

/**
 * A metric's per-run rate: pass/fail evals = the mean of the per-task trial
 * means (the share of trials that passed); scored evals = the share of tasks
 * at or above the pass threshold, else the mean.
 */
function rateOf(metric, perRun) {
  if (!perRun || !(perRun.n > 0)) return null;
  if (metric?.output_type === "pass_fail")
    return isNum(perRun.value) ? perRun.value : null;
  if (isNum(perRun.pass_rate)) return perRun.pass_rate;
  return isNum(perRun.value) ? perRun.value : null;
}

/**
 * Rows for the per-eval table: `[{key, name, kind, rates: {runId: 0..1|null},
 * bestRunId}]`, evals that scored at least one run, the headline success
 * (`primaryKey`) first, then evals and judges, then checks, each by name.
 * `kind` = `judge` | `check` | `eval`.
 * @param {object} analysis  R5
 * @param {{primaryKey?: string}} [opts]
 */
export function evalRateRows(analysis, { primaryKey } = {}) {
  const runs = analysis?.runs ?? [];
  return (analysis?.metrics ?? [])
    .filter((m) => m.family === "eval")
    .map((m) => {
      const rates = Object.fromEntries(
        runs.map((r) => [
          r.run_id,
          // The headline eval reads the run's Success rate (the Matrix
          // rule), the same number as every other screen.
          m.key === primaryKey && isNum(r.success_rate)
            ? r.success_rate
            : rateOf(
                m,
                m.per_run?.find((p) => p.run_id === r.run_id),
              ),
        ]),
      );
      const scored = Object.entries(rates).filter(([, v]) => isNum(v));
      if (!scored.length) return null;
      const hib = m.higher_is_better !== false;
      const bestRunId =
        scored.length > 1
          ? scored.reduce((b, cur) =>
              hib ? (cur[1] > b[1] ? cur : b) : cur[1] < b[1] ? cur : b,
            )[0]
          : null;
      return {
        key: m.key,
        name: m.name ?? m.key,
        kind: String(m.key).startsWith("check:")
          ? "check"
          : /judge|rubric/i.test(m.name ?? "")
            ? "judge"
            : "eval",
        rates,
        bestRunId,
      };
    })
    .filter(Boolean)
    .sort(
      (a, b) =>
        Number(b.key === primaryKey) - Number(a.key === primaryKey) ||
        Number(a.kind === "check") - Number(b.kind === "check") ||
        a.name.localeCompare(b.name),
    );
}
