// HO results R6: which runs `Re-score on vN` offers after Save corrections,
// and its words. Pure.
import { isFinishedRun } from "src/sections/common/Leaderboards/leaderboardModel";

const SCORABLE_OUT = new Set(["external"]);

const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
const joinAnd = (xs) =>
  xs.length <= 2
    ? xs.join(" and ")
    : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`;

/**
 * The finished, listed runs of the dataset scored on a version older than
 * `number` (R1 with config), the dataset evals they carry, and the copy.
 * Null when no run needs it.
 * @param {{results?: object[], metric_defs?: object[]}} r1
 * @param {number} number the version Save corrections made
 */
export function rescoreOnVersionTargets(r1, number) {
  if (!r1 || !Number.isInteger(number)) return null;
  const runs = (r1.results ?? []).filter(
    (r) =>
      r.listed !== false &&
      isFinishedRun(r) &&
      !SCORABLE_OUT.has(r.target_type) &&
      Number.isFinite(r.dataset_version?.number) &&
      r.dataset_version.number < number,
  );
  if (!runs.length) return null;
  const keys = new Set(runs.flatMap((r) => r.metric_keys ?? []));
  const evals = (r1.metric_defs ?? []).filter(
    (d) => d.user_eval_id && keys.has(d.key),
  );
  if (!evals.length) return null;
  const labels = runs.map((r) => r.label).filter(Boolean);
  const who =
    labels.length > 3 ? plural(labels.length, "run") : joinAnd(labels);
  return {
    runs,
    evalIds: evals.map((d) => d.user_eval_id),
    evalNames: evals.map((d) => d.name ?? d.key),
    label: `Re-score ${plural(runs.length, "run")} on v${number}`,
    title: `Re-score ${who} on v${number}?`,
  };
}
