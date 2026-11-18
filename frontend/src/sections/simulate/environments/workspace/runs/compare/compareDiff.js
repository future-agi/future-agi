import { OUTCOME_LABELS } from "../detail/trace/traceTable.constants";

// Show diff: each non-baseline call against the baseline run's call for the
// same scenario row. Client side only.

// Measures where less is better; every other measure is better higher.
export const LOWER_IS_BETTER = new Set([
  "latency",
  "stopLatency",
  "aiInterruptions",
  "duration",
  "tokens",
  "turns",
  "cost",
]);

// The task field, unit and the precision its cell shows, for each measure;
// values compare at that precision, so equal-looking cells draw no arrow.
const toTenth = (v) => Math.round(v * 10) / 10;
const MEASURES = {
  duration: {
    field: "durationMs",
    round: (v) => Math.round(v / 100) * 100,
    format: (v) => `${(v / 1000).toFixed(1)}s`,
  },
  csat: {
    field: "csat",
    round: toTenth,
    format: (v) => v.toLocaleString(undefined, { maximumFractionDigits: 1 }),
  },
  turns: { field: "turns", format: (v) => v.toLocaleString() },
  latency: {
    field: "latencyMs",
    round: Math.round,
    format: (v) => `${Math.round(v).toLocaleString()}ms`,
  },
  stopLatency: {
    field: "stopLatencyMs",
    round: Math.round,
    format: (v) => `${Math.round(v).toLocaleString()}ms`,
  },
  aiInterruptions: {
    field: "aiInterruptions",
    format: (v) => v.toLocaleString(),
  },
  tokens: {
    field: "tokens",
    round: Math.round,
    format: (v) => Math.round(v).toLocaleString(),
  },
};
const atPrecision = (value, round) =>
  value == null || !round ? value : round(value);

/**
 * The baseline call a call is compared with: the same trial, else the
 * baseline's first call; null when the baseline did not run the scenario.
 * @param {Object} task
 * @param {Object[]} baselineCalls
 */
export function baselineCallFor(task, baselineCalls = []) {
  if (!baselineCalls.length) return null;
  return (
    baselineCalls.find((b) => b.trialIndex === task.trialIndex) ??
    baselineCalls[0]
  );
}

/**
 * A change from `before` to `after`, or null when either is missing or equal.
 * @returns {?{ up: boolean, better: boolean, text: string }}
 */
export function delta(measure, after, before, format = (v) => String(v)) {
  if (after == null || before == null) return null;
  const change = after - before;
  if (!Number.isFinite(change) || change === 0) return null;
  const up = change > 0;
  return {
    up,
    better: LOWER_IS_BETTER.has(measure) ? !up : up,
    text: format(Math.abs(change)),
  };
}

const metSubGoals = (task) =>
  (task.subGoalResults ?? []).filter((g) => g.passed === true).length;
const scoreOf = (task, evalId) =>
  task.evalResults?.find((r) => r.id === evalId)?.score ?? null;

/**
 * Every change on one call against its baseline call.
 * @param {Object} task
 * @param {?Object} base
 * @param {string[]} evalIds
 * @returns {?{ was: ?string, subGoals: ?Object, measures: Object, evals: Object }}
 */
export function callDiff(task, base, evalIds = []) {
  if (!base || base.id === task.id) return null;
  const measures = Object.fromEntries(
    Object.entries(MEASURES).map(([key, { field, round, format }]) => [
      key,
      delta(
        key,
        atPrecision(task[field], round),
        atPrecision(base[field], round),
        format,
      ),
    ]),
  );
  const evals = Object.fromEntries(
    evalIds.map((id) => {
      const after = scoreOf(task, id);
      const before = scoreOf(base, id);
      return [
        id,
        after == null || before == null
          ? null
          : delta(
              id,
              Math.round(after * 100),
              Math.round(before * 100),
              (v) => `${v} pts`,
            ),
      ];
    }),
  );
  const subGoals =
    task.subGoalResults?.length || base.subGoalResults?.length
      ? delta("subGoals", metSubGoals(task), metSubGoals(base))
      : null;
  return {
    was:
      task.status !== base.status
        ? OUTCOME_LABELS[base.status] ?? OUTCOME_LABELS.inconclusive
        : null,
    subGoals,
    measures,
    evals,
  };
}
