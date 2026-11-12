// options-truth L3 G9 (fix 9): how a Test a fix child's per-try time and
// cost move with its tool modes and its cost cap. One rule for the dock's
// estimate and for the run the mock world builds (childProfile), so the
// estimate the dock shows is the cost the child records. Pure.

/** [takes time, costs] per tool mode. */
export const TOOL_MODE_WEIGHTS = Object.freeze({
  mock: Object.freeze([0, 0]),
  recorded: Object.freeze([1, 0]),
  live: Object.freeze([1, 1]),
});
// Tools hold half a try's time and a quarter of its cost when every tool
// takes time and costs (a Live run); Mock answers at once and costs nothing.
const TIME_SHARE = 0.5;
const COST_SHARE = 0.25;

const weightOf = (tools, i) =>
  tools.reduce(
    (s, t) =>
      s + (TOOL_MODE_WEIGHTS[t?.mode ?? "live"] ?? TOOL_MODE_WEIGHTS.live)[i],
    0,
  );

/**
 * Multipliers on a parent's per-try time and cost when its tools change
 * modes: `{latency, cost}` (1 and 1 when nothing changes or the tool lists
 * differ in length).
 * @param {Array<{name, mode}>} before  the parent's tools
 * @param {Array<{name, mode}>} after  the child's tools
 */
export function toolModeFactors(before = [], after = []) {
  const n = (after ?? []).length;
  if (!n || (before ?? []).length !== n) return { latency: 1, cost: 1 };
  const share = (tools, i, part) => 1 - part + (part * weightOf(tools, i)) / n;
  return {
    latency: share(after, 0, TIME_SHARE) / share(before, 0, TIME_SHARE),
    cost: share(after, 1, COST_SHARE) / share(before, 1, COST_SHARE),
  };
}

/** The child's tools: the parent's with the draft's modes applied. */
export function toolsWithModes(tools = [], modes = {}) {
  return (tools ?? []).map((t) =>
    modes?.[t.name] ? { ...t, mode: modes[t.name] } : t,
  );
}

/** A try never records more than its cost cap (null cap = no cap). */
export function cappedPerTry(perTry, cap) {
  if (!Number.isFinite(perTry)) return perTry;
  const c = cap == null || cap === "" ? null : Number(cap);
  return Number.isFinite(c) && c > 0 ? Math.min(perTry, c) : perTry;
}
