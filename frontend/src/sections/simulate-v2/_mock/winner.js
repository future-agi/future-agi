/**
 * Picking a winner.
 *
 * Six runs, a dozen numbers each, and no two of them agree — the run with the
 * best pass rate is rarely also the fastest or the cheapest. "Which one wins"
 * is therefore not a fact about the runs; it is a question about what this
 * team is optimising for, and the only honest way to answer it is to make them
 * say so first.
 *
 * So a winner is a weighting, applied. The weights are the argument, the
 * ranking is arithmetic, and both are kept with the result — a winner nobody
 * can see the reasoning behind is just a badge.
 */
import { versionNumber } from "./versions";
import { creditsLabel } from "./metering";

/** Which way is better for each metric — half of them are lower-is-better. */
export const SYSTEM_METRICS = [
  { id: "duration", label: "Average response time", group: "system", lowerIsBetter: true, get: (r) => r.avgDurationMs, format: (v) => `${(v / 1000).toFixed(1)}s` },
  { id: "tokens", label: "Tokens", group: "system", lowerIsBetter: true, get: (r) => r.tokens, format: (v) => v.toLocaleString() },
  /* Spend is said in credits; unverified call costs are never in `cost`. */
  { id: "cost", label: "Cost", group: "system", lowerIsBetter: true, get: (r) => r.cost, format: (v) => creditsLabel(v) },
];

/**
 * The quality side: the pass rate, every grader applied to the environment,
 * and the count of claims the call log could not support — which belongs here
 * rather than with the system numbers, because saying a refund was issued and
 * not issuing it is a quality failure that every word-reading grader misses.
 */
export const evalMetrics = (evals = []) => [
  { id: "passRate", label: "Pass rate", group: "eval", lowerIsBetter: false, get: (r) => r.passRate, format: (v) => `${Math.round(v)}%` },
  /* The environment's own score. A team optimising for training wants this
     weighted, not the pass rate — and they are not always the same ordering. */
  { id: "meanReturn", label: "Mean return", group: "eval", lowerIsBetter: false, get: (r) => r.meanReturn ?? 0, format: (v) => v.toFixed(2) },
  ...evals.map((e) => ({
    id: `eval:${e.id}`,
    label: e.name,
    group: "eval",
    lowerIsBetter: false,
    get: (r) => r.scores?.[e.id] ?? 0,
    format: (v) => `${Math.round(v)}%`,
    /* Sub-goals decide pass or fail; an evaluation only decides when it's
       marked required — so only then does it weigh like one. */
    required: !!(e.required || e.applied?.required),
  })),
  { id: "saidNotDone", label: "Said, not done", group: "eval", lowerIsBetter: true, get: (r) => r.saidNotDone, format: (v) => `${v}` },
];

export const allMetrics = (evals) => [...evalMetrics(evals), ...SYSTEM_METRICS];

/**
 * Presets, because most teams have one of four opinions and typing six sliders
 * to express a common one is a tax. Each is a starting point — every preset
 * leaves the sliders live.
 *
 * In every one the pass rate leads: sub-goals decide pass or fail, so a
 * winner that passes less isn't a winner. A required evaluation weighs
 * almost as much; an optional one only scores, so it only breaks ties.
 */
const qualityWeight = (m, { pass, required, optional, other }) => (
  m.id === "passRate" ? pass
    : m.id === "saidNotDone" ? Math.max(other, 6)
      : m.id.startsWith("eval:") ? (m.required ? required : optional)
        : other
);
export const WEIGHT_PRESETS = [
  {
    id: "balanced",
    label: "Balanced",
    icon: "solar:scale-linear",
    color: "#CA8A04",
    blurb: "Pass rate leads; speed, cost and scores break ties.",
    weights: (m) => (m.group === "system" ? 4 : qualityWeight(m, { pass: 10, required: 8, optional: 2, other: 3 })),
  },
  {
    id: "quality",
    label: "Quality first",
    icon: "solar:like-linear",
    color: "#16A34A",
    blurb: "Correctness over speed and spend.",
    weights: (m) => (m.group === "system" ? 1 : qualityWeight(m, { pass: 10, required: 9, optional: 5, other: 5 })),
  },
  {
    id: "speed",
    label: "Speed first",
    icon: "solar:bolt-linear",
    color: "#2563EB",
    blurb: "Latency leads, quality still has to hold.",
    weights: (m) => (m.id === "duration" ? 10 : m.group === "system" ? 3 : qualityWeight(m, { pass: 8, required: 6, optional: 1, other: 2 })),
  },
  {
    id: "cost",
    label: "Cost efficient",
    icon: "solar:dollar-minimalistic-linear",
    color: "#EA580C",
    blurb: "Cheapest run that still passes.",
    weights: (m) => (m.id === "cost" ? 10 : m.id === "tokens" ? 7 : m.group === "system" ? 3 : qualityWeight(m, { pass: 8, required: 6, optional: 1, other: 2 })),
  },
];

export const presetWeights = (preset, metrics) =>
  Object.fromEntries(metrics.map((m) => [m.id, preset.weights(m)]));

export const defaultWeights = (metrics) =>
  presetWeights(WEIGHT_PRESETS[0], metrics);

/**
 * Rank the runs.
 *
 * Each metric is normalised across the runs being compared rather than against
 * an absolute scale, because there isn't one: 10s is fast for a support call
 * and slow for a lookup. So a run scores by where it sits between the best and
 * worst run on that metric, and the weights decide how much that placement
 * counts.
 *
 * A metric every run ties on contributes nothing either way — normalising it
 * to zero would silently punish everyone.
 */
/**
 * The runs a winner can be picked from: the ones that took the same test.
 * Same environment version (the newest any of them used — the version people
 * see, via `versionOf`), the full scenario set, no tools the world couldn't
 * answer, and the same number of trials — otherwise "best" is partly "had an
 * easier exam".
 */
export const comparableRuns = (summaries = [], versionOf = (r) => r.envVersion, target = null) => {
  const envs = [...new Set(summaries.map(versionOf).filter(Boolean))];
  /* `target` — rank on that version only (the one in use), even when no run
     has taken it yet; otherwise the newest any run used. */
  const envVersion = target || envs.sort((a, b) => versionNumber(b) - versionNumber(a))[0] || null;
  const onEnv = summaries.filter((r) => (!envVersion || versionOf(r) === envVersion)
    && !r.partial && !(r.toolGap || []).length);
  const counts = onEnv.reduce((m, r) => m.set(r.repeats || 1, (m.get(r.repeats || 1) || 0) + 1), new Map());
  const repeats = [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ?? null;
  const runs = onEnv.filter((r) => (r.repeats || 1) === repeats);
  return { runs, envVersion, repeats, excluded: summaries.length - runs.length };
};

export const rankRuns = (summaries, metrics, weights) => {
  if (!summaries.length) return [];

  const ranges = Object.fromEntries(
    metrics.map((m) => {
      const values = summaries.map((r) => Number(m.get(r)) || 0);
      return [m.id, { min: Math.min(...values), max: Math.max(...values) }];
    }),
  );

  const totalWeight = metrics.reduce((a, m) => a + (weights[m.id] ?? 0), 0);

  const scored = summaries.map((run) => {
    const contributions = metrics.map((m) => {
      const w = weights[m.id] ?? 0;
      const { min, max } = ranges[m.id];
      const raw = Number(m.get(run)) || 0;
      /* Everyone tied: neutral, so a flat metric cannot decide the winner. */
      const placed = max === min ? 0.5 : (raw - min) / (max - min);
      const normalised = m.lowerIsBetter ? 1 - placed : placed;
      return { metric: m, weight: w, value: raw, normalised, weighted: normalised * w };
    });

    const score = totalWeight
      ? contributions.reduce((a, c) => a + c.weighted, 0) / totalWeight
      : 0;

    return { run, score, contributions };
  });

  return scored.sort((a, b) => b.score - a.score);
};

/** What decided it: the metrics that separated the winner from the runner-up. */
export const winningMargins = (ranked, limit = 2) => {
  const [first, second] = ranked;
  if (!first || !second) return [];
  return first.contributions
    .map((c, i) => ({
      label: c.metric.label,
      gain: c.weighted - second.contributions[i].weighted,
      value: c.metric.format(c.value),
    }))
    .filter((d) => d.gain > 0.001)
    .sort((a, b) => b.gain - a.gain)
    .slice(0, limit);
};

/**
 * One metric, read against the baseline run.
 *
 * Rates are compared in percentage points and everything else in percent,
 * which is not a stylistic choice: 43% → 86% is +43 points or +100% relative,
 * and only one of those is the sentence anyone means. Points also survive a
 * baseline of zero, which a relative change cannot.
 */
export const deltaAgainst = (metric, run, baselineRun) => {
  if (!metric || !baselineRun || run.id === baselineRun.id) return null;

  const now = Number(metric.get(run)) || 0;
  const before = Number(metric.get(baselineRun)) || 0;
  if (now === before) return { text: "same", better: null, flat: true };

  const isRate = metric.id === "passRate" || metric.id.startsWith("eval:");
  const up = now > before;

  const text = isRate
    ? `${Math.abs(Math.round(now - before))} pts`
    : before === 0
      ? `${Math.abs(now - before) < 1 ? Math.abs(now - before).toFixed(2) : Math.abs(Math.round(now - before))}`
      : `${Math.abs(Math.round(((now - before) / before) * 100))}%`;

  return { text, up, better: metric.lowerIsBetter ? !up : up, flat: false };
};

/**
 * The change in scenario pass rate between two runs, read from the numbers
 * the list displays (aligned stats), so a "57%" under a "66%" says -9 and
 * nothing else. `noise` is the 95% chance spread of two independent samples;
 * inside it the change is `within`.
 */
export const statsDeltaOf = (st, was) => {
  if (!st?.measured || !was?.measured) return null;
  const p1 = st.scenarioPassed / st.measured;
  const p2 = was.scenarioPassed / was.measured;
  const pts = (st.scenarioRate ?? 0) - (was.scenarioRate ?? 0);
  const noise = Math.max(
    1,
    Math.round(1.96 * Math.sqrt((p1 * (1 - p1)) / st.measured + (p2 * (1 - p2)) / was.measured) * 100),
  );
  return { pts, noise, within: Math.abs(pts) <= noise, shared: Math.min(st.measured, was.measured) };
};

/*
  Winning is not shipping. A winner is whichever run best fits the weights
  someone chose — a preference, applied consistently. Whether that agent is
  fit to ship is the user's call, made outside the harness (PRD v7 §4.10.1);
  the run summary (runRollup.js) is the evidence beside it, never a gate.
*/
