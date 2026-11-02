import { BUILD_TONES } from "../../../buildEnvironment/buildTones";

// Eval identity colours, indexed by the eval's position in the derived set —
// green first, purple second, matching the designer's Task-success / Policy
// graph. Mapped onto BUILD_TONES so this module carries no raw hex.
export const EVAL_COLORS = [
  BUILD_TONES.green,
  BUILD_TONES.accent,
  BUILD_TONES.blue,
  BUILD_TONES.amber,
  BUILD_TONES.orange,
  BUILD_TONES.pink,
  BUILD_TONES.tealDeep,
  BUILD_TONES.indigo,
];

// A backend score key ("task_success") → a display name ("Task success"). The
// kpis payload keys the eval metrics by snake_case; the graph legend and the
// table header want the human label. Sentence case (first word capitalised
// only), matching the designer.
function humanizeEvalKey(key) {
  const s = String(key).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Pure adapters for the Runs summary — the populated Runs tab's graph + table.
//
// A summary row is one completed run, read against the environment's applied
// evals. Real columns (pass rate, tasks, avg duration) come from the executions
// row; per-eval scores come from that run's kpis (or, under the QA switch, from
// the mock run itself). Tokens and cost have no field on the runs list, so they
// are null here and render as a dashed cell, never a fabricated number.

// One run → the summary table's row shape. `scores` is the run's {evalId: 0–100}
// map (kpis-derived for a real run, inline for a mock one); absent → {}.
export function buildSummaryRow(run, scores) {
  const total = run?.total ?? 0;
  const passed = run?.passed ?? 0;
  return {
    id: run?.id,
    executionId: run?.executionId ?? run?.id,
    ordinal: run?.ordinal,
    label: run?.label,
    agentVersion: run?.agentVersion ?? null,
    pinnedAgentVersion: run?.pinnedAgentVersion ?? null,
    environmentVersion: run?.environmentVersion ?? null,
    agentDiffers: Boolean(run?.agentDiffers),
    status: run?.status,
    runState: run?.runState ?? run?.status,
    stoppable: !!run?.stoppable,
    at: run?.finishedAt || run?.startedAt || null,
    finishedAt: run?.finishedAt ?? null,
    tasks: total,
    passed,
    failed: run?.failed ?? Math.max(total - passed, 0),
    scenarioCount: run?.scenarioCount ?? null,
    scenarioIds: run?.scenarioIds ?? [],
    trials: run?.trials ?? 1,
    passRate: total ? Math.round((passed / total) * 100) : 0,
    durationS: run?.durationS ?? null,
    scores: scores || {},
    tokens: null,
    cost: null,
  };
}

// Distinct scenarios the runs covered. Runs that carry no ids (older or mock
// runs) can't add to the union, so they are skipped; only when no run has ids
// does the environment total stand in.
export function countCoveredScenarios(rows, fallback) {
  const keyed = (rows || []).filter((r) => r?.scenarioIds?.length);
  if (!keyed.length) return fallback;
  return new Set(keyed.flatMap((r) => r.scenarioIds)).size;
}

// The environment's eval set for the summary, derived from the union of the
// runs' score keys (first-seen order) — so the graph lines and the table's eval
// columns come from the same source the scores do, and always agree. Each entry
// carries a humanised name and a stable identity colour. No scores → no evals.
export function deriveEvals(rows) {
  const seen = [];
  (rows || []).forEach((r) => {
    Object.keys(r?.scores || {}).forEach((key) => {
      if (!seen.includes(key)) seen.push(key);
    });
  });
  return seen.map((id, i) => ({
    id,
    name: humanizeEvalKey(id),
    color: EVAL_COLORS[i % EVAL_COLORS.length],
  }));
}

// A run the pass-rate chart plots: it finished with a verdict on at least one
// call. Running and stopped runs are left off, since their pass rate counts
// calls that never ran.
// Reads a summary row too (it counts its calls as `tasks`).
export function isFinishedRun(run) {
  return (
    !!run?.finishedAt &&
    (run?.total ?? run?.tasks ?? 0) > 0 &&
    (run?.status === "passed" || run?.status === "failed")
  );
}

// The numbers a run is read against a baseline on, and the winner ranked on:
// only what the runs list and each run's kpis send. `kind` sets how a change
// reads: a rate in points, a count as itself, anything else in percent.
export const PASS_METRIC = {
  id: "passRate",
  label: "Pass rate",
  group: "eval",
  kind: "rate",
  get: (r) => r?.passRate,
  format: (v) => `${Math.round(v)}%`,
};
export const FAILED_METRIC = {
  id: "failed",
  label: "Failing",
  kind: "count",
  lowerIsBetter: true,
  get: (r) => r?.failed,
};
export const RUN_TIME_METRIC = {
  id: "duration",
  label: "Run time",
  group: "system",
  kind: "relative",
  lowerIsBetter: true,
  get: (r) => r?.durationS,
};
export const evalMetric = (e) => ({
  id: `eval:${e.id}`,
  label: e.name,
  group: "eval",
  kind: "rate",
  get: (r) => r?.scores?.[e.id],
  format: (v) => `${Math.round(v)}%`,
});

// What Choose winner weighs: the pass rate, every eval, and the run time.
export const winnerMetrics = (evals = []) => [
  PASS_METRIC,
  ...evals.map(evalMetric),
  RUN_TIME_METRIC,
];

const valueOf = (metric, row) => {
  const v = metric.get(row);
  return v == null || Number.isNaN(Number(v)) ? null : Number(v);
};

// One number on `row` against the same number on the baseline run. Rates
// move in points (43% to 86% is 43 points, not 100%), counts by how many,
// the rest in percent. Null when either side has no value, or `row` is the
// baseline itself. `better` reads lower-is-better metrics the right way up.
export function deltaAgainst(metric, row, baseline) {
  if (!metric || !row || !baseline || row.id === baseline.id) return null;
  const now = valueOf(metric, row);
  const before = valueOf(metric, baseline);
  if (now == null || before == null) return null;
  if (now === before) return { flat: true };
  const up = now > before;
  const diff = Math.abs(now - before);
  const better = metric.lowerIsBetter ? !up : up;
  if (metric.kind === "rate") {
    return { text: `${Math.round(diff)} pts`, up, better };
  }
  if (metric.kind === "count") {
    return {
      text: `${diff}`,
      label: `${diff} ${up ? "more" : "fewer"}`,
      up,
      better,
    };
  }
  const text =
    before === 0
      ? `${Math.round(diff)}`
      : `${Math.round((diff / Math.abs(before)) * 100)}%`;
  return { text, up, better };
}

// Weight presets. The pass rate leads in each; evals only break ties, since
// none is marked required yet. The cost preset has no cost or tokens to
// weigh, so the run time stands in at the system weight.
export const WEIGHT_PRESETS = [
  {
    id: "balanced",
    label: "Balanced",
    icon: "solar:scale-linear",
    color: BUILD_TONES.amber,
    blurb: "Pass rate leads; speed and scores break ties.",
    weights: { pass: 10, evals: 2, system: 4 },
  },
  {
    id: "quality",
    label: "Quality first",
    icon: "solar:like-linear",
    color: BUILD_TONES.green,
    blurb: "Correctness over speed.",
    weights: { pass: 10, evals: 5, system: 1 },
  },
  {
    id: "speed",
    label: "Speed first",
    icon: "solar:bolt-linear",
    color: BUILD_TONES.blue,
    blurb: "Run time leads, quality still has to hold.",
    weights: { pass: 8, evals: 1, system: 10 },
  },
  {
    id: "cost",
    label: "Cost efficient",
    icon: "solar:dollar-minimalistic-linear",
    color: BUILD_TONES.orange,
    blurb: "Cheapest run that still passes.",
    weights: { pass: 8, evals: 1, system: 3 },
  },
];

export const presetWeights = (preset, metrics) =>
  Object.fromEntries(
    metrics.map((m) => {
      if (m.id === PASS_METRIC.id) return [m.id, preset.weights.pass];
      if (m.group === "system") return [m.id, preset.weights.system];
      return [m.id, preset.weights.evals];
    }),
  );

// The runs a winner is picked from: finished ones that took the same number
// of repeats as most of them, so "best" isn't partly "had an easier test".
export function comparableRuns(rows = []) {
  const done = rows.filter(isFinishedRun);
  const counts = new Map();
  done.forEach((r) => {
    const n = r.trials || 1;
    counts.set(n, (counts.get(n) || 0) + 1);
  });
  const repeats =
    [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ??
    null;
  const runs = done.filter((r) => (r.trials || 1) === repeats);
  return { runs, repeats, excluded: rows.length - runs.length };
}

// Each metric is placed between the best and worst run on it, then weighted.
// A metric every run ties on scores them all the same; a run with no value
// for a metric places last on it.
export function rankRuns(rows, metrics, weights) {
  if (!rows.length) return [];
  const ranges = Object.fromEntries(
    metrics.map((m) => {
      const values = rows.map((r) => valueOf(m, r)).filter((v) => v != null);
      return [
        m.id,
        values.length
          ? { min: Math.min(...values), max: Math.max(...values) }
          : null,
      ];
    }),
  );
  const totalWeight = metrics.reduce((a, m) => a + (weights[m.id] ?? 0), 0);
  return rows
    .map((run) => {
      const contributions = metrics.map((m) => {
        const weight = weights[m.id] ?? 0;
        const range = ranges[m.id];
        const value = valueOf(m, run);
        let normalised = 0;
        if (value != null && range) {
          const placed =
            range.max === range.min
              ? 0.5
              : (value - range.min) / (range.max - range.min);
          normalised = m.lowerIsBetter ? 1 - placed : placed;
        }
        return { metric: m, weight, normalised, weighted: normalised * weight };
      });
      const score = totalWeight
        ? contributions.reduce((a, c) => a + c.weighted, 0) / totalWeight
        : 0;
      return { run, score, contributions };
    })
    .sort((a, b) => b.score - a.score);
}

// What separated the winner from the runner-up, biggest gap first.
export function winningMargins(ranked, limit = 2) {
  const [first, second] = ranked;
  if (!first || !second) return [];
  return first.contributions
    .map((c, i) => ({
      label: c.metric.label,
      gain: c.weighted - second.contributions[i].weighted,
    }))
    .filter((d) => d.gain > 0.001)
    .sort((a, b) => b.gain - a.gain)
    .slice(0, limit);
}
