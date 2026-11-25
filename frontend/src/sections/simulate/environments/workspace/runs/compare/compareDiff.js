import { OUTCOME_LABELS } from "../detail/trace/traceTable.constants";

// Show diff: the server compares each call with the baseline's; this only
// words its changes the way each cell shows its value.

const FORMATS = {
  duration: (v) => `${(v / 1000).toFixed(1)}s`,
  csat: (v) => v.toLocaleString(undefined, { maximumFractionDigits: 1 }),
  turns: (v) => v.toLocaleString(),
  latency: (v) => `${Math.round(v).toLocaleString()}ms`,
  stopLatency: (v) => `${Math.round(v).toLocaleString()}ms`,
  aiInterruptions: (v) => v.toLocaleString(),
  tokens: (v) => Math.round(v).toLocaleString(),
};

const worded = (change, format = (v) => String(v)) =>
  change
    ? { up: change.up, better: change.better, text: format(change.change) }
    : null;

const mapValues = (obj, fn) =>
  Object.fromEntries(Object.entries(obj ?? {}).map(([k, v]) => [k, fn(v, k)]));

/**
 * A call's `diff` from the server, worded for its cells.
 * @param {?{ was: ?string, subGoals: ?Object, measures: Object, evals: Object }} diff
 * @returns {?{ was: ?string, subGoals: ?Object, measures: Object, evals: Object }}
 */
export function wordDiff(diff) {
  if (!diff) return null;
  return {
    was: diff.was
      ? OUTCOME_LABELS[diff.was] ?? OUTCOME_LABELS.inconclusive
      : null,
    subGoals: worded(diff.subGoals),
    measures: mapValues(diff.measures, (change, key) =>
      worded(change, FORMATS[key]),
    ),
    evals: mapValues(diff.evals, (change) => worded(change, (v) => `${v} pts`)),
  };
}
