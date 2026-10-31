import { agentVersionLabel, passRateOf } from "./compareFormat";

const mean = (values) => {
  const xs = values.filter((v) => v != null);
  return xs.length
    ? Math.round(xs.reduce((a, v) => a + v, 0) / xs.length)
    : null;
};

const sum = (values) => {
  const xs = values.filter((v) => v != null);
  return xs.length ? xs.reduce((a, v) => a + v, 0) : null;
};

/**
 * The comparison as a JSON document: the runs, and once the calls are in,
 * every scenario with each run's verdict and the verdict over the pairs.
 * Everything in it is read from the API; nothing is estimated.
 */
export function comparisonPayload({
  env,
  runs,
  comparison = null,
  read = null,
}) {
  return {
    environment: { id: env?.id ?? null, name: env?.name ?? null },
    baseline: runs[0]?.id ?? null,
    verdict: read
      ? {
          label: read.label.label,
          comparedOn: read.interval.n,
          delta: read.interval.delta,
          interval: [read.interval.lo, read.interval.hi],
        }
      : null,
    runs: runs.map((r) => ({
      id: r.id,
      run: r.label,
      agentVersion: agentVersionLabel(r.agentVersion),
      passed: r.passed,
      total: r.total,
      passRate: passRateOf(r),
      repeats: r.trials || 1,
    })),
    scenarios: (comparison?.rows || []).map((row) => ({
      scenario: row.scenarioKey,
      goal: row.goal ?? null,
      pair: row.pair?.cls ?? null,
      movement: row.pair?.flip?.text ?? null,
      runs: runs.map((r) => {
        const trials = row.cells[r.id] || [];
        const verdict = row.verdicts[r.id];
        return {
          run: r.id,
          verdict: verdict?.status ?? "missing",
          passes: verdict?.passes ?? 0,
          measured: verdict?.measured ?? 0,
          calls: trials.length,
          durationMs: mean(trials.map((t) => t.durationMs)),
          tokens: sum(trials.map((t) => t.tokens)),
        };
      }),
    })),
  };
}

// Saves the payload as `<environment>-compare.json`.
export function downloadComparison(payload) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `${payload.environment.id || "comparison"}-compare.json`;
  a.click();
  URL.revokeObjectURL(url);
}
