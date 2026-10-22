/*
  Improvements level 2 — the runs one improvement tried, as rows and as
  points on its pass-rate chart.
*/
import { triedLabelOf } from "../_mock/optimizer";

/** Each run (trial) the search scored, oldest first — the order it tried them in. */
export const trialRowsFor = (env, envState, record, candidates = []) => {
  const r = record?.result;
  if (!r?.trials?.length) return [];
  const byN = new Map(candidates.map((c) => [c.n, c]));
  const start = Date.parse(record.completedAt || record.createdAt || 0) || 0;
  return r.trials.map((t, i) => {
    const c = byN.get(t.n);
    return {
      runId: `${record.id}-t${t.n}`,
      n: t.n,
      name: `Run ${t.n}`,
      tried: triedLabelOf(t, r.trials),
      /* A retry that learned from the last run: what it read and what it adds. */
      feedback: t.feedback || null,
      improved: !!t.improved,
      score: Math.round(t.score),
      fixed: t.fixed || 0,
      broke: t.broke || 0,
      /* Why it can't win — cheated, a must-pass failure, over a limit. */
      why: c && !c.eligible ? c.why : null,
      costDelta: c?.cost != null ? Math.round(c.cost) : null,
      createdAt: new Date(start + i * 60000).toISOString(),
    };
  });
};

/**
 * A run as the pass-rate chart reads one: its target scenarios, with as
 * many passing as its score says. Every run tried a candidate on the same
 * version, so they share one version label.
 */
export const chartRunOf = (row, record) => {
  const t = (record?.result?.trials || []).find((x) => x.n === row.n);
  const ids = Object.keys(t?.perScenario || {});
  /* Scored on the target scenarios — as many as the search measured. */
  const total = record?.result?.trainMeasured || ids.length || 10;
  const passed = Math.round((row.score / 100) * total);
  const base = record?.baseAgentVersion || null;
  return {
    id: row.runId,
    kind: "run",
    ordinal: row.n,
    agentVersion: base ? `${base} + change` : "change",
    startedAt: row.createdAt,
    finishedAt: row.createdAt,
    repeats: 1,
    scores: {},
    tasks: Array.from({ length: total }, (_, k) => ({ id: ids[k] || `s${k}`, scenarioId: ids[k] || `s${k}`, status: k < passed ? "passed" : "failed" })),
    /* Its own score, as the table shows it — not re-counted off rounded tasks. */
    stats: { measured: total, scenarioPassed: passed, scenarioRate: row.score, callRate: row.score, calls: total, flaky: 0, failed: total - passed, tookMs: null },
  };
};
