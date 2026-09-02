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

/** Which way is better for each metric — half of them are lower-is-better. */
export const SYSTEM_METRICS = [
  { id: "duration", label: "Average response time", group: "system", lowerIsBetter: true, get: (r) => r.avgDurationMs, format: (v) => `${(v / 1000).toFixed(1)}s` },
  { id: "tokens", label: "Tokens", group: "system", lowerIsBetter: true, get: (r) => r.tokens, format: (v) => v.toLocaleString() },
  { id: "cost", label: "Cost", group: "system", lowerIsBetter: true, get: (r) => r.cost, format: (v) => `$${v.toFixed(2)}` },
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
  })),
  { id: "saidNotDone", label: "Said, not done", group: "eval", lowerIsBetter: true, get: (r) => r.saidNotDone, format: (v) => `${v}` },
];

export const allMetrics = (evals) => [...evalMetrics(evals), ...SYSTEM_METRICS];

/**
 * Presets, because most teams have one of four opinions and typing six sliders
 * to express a common one is a tax. Each is a starting point — every preset
 * leaves the sliders live.
 */
export const WEIGHT_PRESETS = [
  {
    id: "balanced",
    label: "Balanced",
    icon: "solar:scale-linear",
    color: "#CA8A04",
    blurb: "Everything counts the same.",
    weights: () => 5,
  },
  {
    id: "quality",
    label: "Quality first",
    icon: "solar:like-linear",
    color: "#16A34A",
    blurb: "Correctness over speed and spend.",
    weights: (m) => (m.group === "eval" ? 9 : 2),
  },
  {
    id: "speed",
    label: "Speed first",
    icon: "solar:bolt-linear",
    color: "#2563EB",
    blurb: "Latency leads, quality still has to hold.",
    weights: (m) => (m.id === "duration" ? 10 : m.group === "eval" ? 5 : 3),
  },
  {
    id: "cost",
    label: "Cost efficient",
    icon: "solar:dollar-minimalistic-linear",
    color: "#EA580C",
    blurb: "Cheapest run that still passes.",
    weights: (m) => (m.id === "cost" ? 10 : m.id === "tokens" ? 7 : m.group === "eval" ? 5 : 3),
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

/* ── the release gate ─────────────────────────────────────────────────────── */

/**
 * Winning is not shipping.
 *
 * A winner is whichever run best fits the weights someone chose — a preference,
 * applied consistently. It says nothing about whether the run is fit to
 * release, and a screen that crowns a run and offers no other verdict quietly
 * invites the first to be read as the second. The gate is the second verdict,
 * and unlike the weights it is not up for negotiation on the day: a release
 * blocker that fails is a blocker that fails.
 *
 * Two tiers, deliberately. Hard checks block; soft checks are things a human
 * can knowingly accept, and hiding them among the blockers would train people
 * to override blockers as a matter of routine.
 */
/**
 * What a run is allowed to cost.
 *
 * Cost and latency are reported on every screen and constrain nothing, which
 * makes them decoration. A budget turns them into a decision: an agent that
 * passes every check and doubles the bill is not obviously shippable, and the
 * person who has to defend that trade should have said the number in advance
 * rather than after the invoice.
 */
export const DEFAULT_BUDGETS = { costPerRun: 2.5, latencyMs: 2000 };

export const releaseGate = (run, { baseline, scenarioCount, budgets } = {}) => {
  if (!run) return null;

  const tasks = run.tasks || [];
  /* Scenarios that produced no verdict. Not failures — but not passes either,
     and a gate that treats "we never measured it" as clearance is the reason
     these gates exist. */
  const unmeasured = tasks.filter((t) => t.status === "unmeasured");
  const blockers = tasks.filter((t) => t.critical);
  const blockersFailed = blockers.filter((t) => t.status === "failed");
  const blockersFlaky = blockers.filter((t) => t.status === "flaky");

  /* A blocker that passed on the baseline and does not now is the single most
     expensive thing this product can fail to say out loud. */
  const regressed = baseline && baseline.id !== run.id
    ? blockers.filter((t) => {
      const before = baseline.tasks?.find((b) => b.id === t.id);
      return before?.status === "passed" && t.status !== "passed";
    })
    : [];

  const covered = scenarioCount ? tasks.length >= scenarioCount : true;
  const unsupported = tasks.filter((t) => t.callLog?.unsupportedClaim).length;
  const flaky = tasks.filter((t) => t.status === "flaky").length;

  const budget = { ...DEFAULT_BUDGETS, ...(budgets || {}) };

  const checks = [
    {
      id: "budget-cost",
      hard: false,
      label: `Run cost within $${budget.costPerRun.toFixed(2)}`,
      ok: (run.cost || 0) <= budget.costPerRun,
      detail: `$${(run.cost || 0).toFixed(2)} this run`,
    },
    {
      id: "budget-latency",
      hard: false,
      label: `Average response under ${budget.latencyMs}ms`,
      ok: (run.avgDurationMs || 0) <= budget.latencyMs * 8,
      detail: `${Math.round((run.avgDurationMs || 0) / 1000)}s average task, ${Math.round((run.avgDurationMs || 0) / 8)}ms per turn`,
    },
    {
      id: "blockers",
      hard: true,
      label: "Every release blocker passes",
      ok: blockersFailed.length === 0,
      detail: blockersFailed.length
        ? `${blockersFailed.length} failing: ${blockersFailed.slice(0, 2).map((t) => t.title).join(", ")}${blockersFailed.length > 2 ? "…" : ""}`
        : `${blockers.length} blockers, all passing`,
    },
    {
      id: "regression",
      hard: true,
      label: "No blocker regressed against the baseline",
      ok: regressed.length === 0,
      detail: regressed.length
        ? `${regressed.length} passed on run ${baseline.ordinal} and no longer does`
        : baseline && baseline.id !== run.id ? `Checked against run ${baseline.ordinal}` : "No baseline to compare against",
    },
    {
      id: "measured",
      hard: true,
      label: "Every scenario produced a verdict",
      ok: unmeasured.length === 0,
      detail: unmeasured.length
        ? `${unmeasured.length} could not be measured — ${[...new Set(unmeasured.map((t) => (t.fault?.environment && "environment") || (t.fault?.transport && "transport") || (t.fault?.simulator && "simulator") || "grading"))].join(", ")}. Re-run before reading this rate.`
        : "Nothing was lost to the builder",
    },
    {
      id: "coverage",
      hard: true,
      label: "Ran the full scenario set",
      ok: covered,
      /* A subset run can win a comparison on points. It cannot clear a gate:
         the scenarios it skipped are the ones nobody looked at. */
      detail: covered ? `${tasks.length} scenarios` : `${tasks.length} of ${scenarioCount} — the rest were not run`,
    },
    {
      id: "blockerFlake",
      hard: false,
      label: "No blocker is flaky",
      ok: blockersFlaky.length === 0,
      detail: blockersFlaky.length
        ? `${blockersFlaky.length} decided differently across samples`
        : "Blockers were consistent across samples",
    },
    {
      id: "claims",
      hard: false,
      label: "Nothing said that the call log cannot support",
      ok: unsupported === 0,
      detail: unsupported ? `${unsupported} claims with no matching tool call` : "Every claim matched a tool call",
    },
    {
      id: "flake",
      hard: false,
      label: "Flakiness under 10% of scenarios",
      ok: !tasks.length || flaky / tasks.length <= 0.1,
      detail: flaky ? `${flaky} of ${tasks.length} scenarios disagreed with themselves` : "No flaky scenarios",
    },
  ];

  const blocked = checks.filter((c) => c.hard && !c.ok);
  const warnings = checks.filter((c) => !c.hard && !c.ok);

  return {
    checks,
    blocked,
    warnings,
    status: blocked.length ? "blocked" : warnings.length ? "warn" : "clear",
  };
};
