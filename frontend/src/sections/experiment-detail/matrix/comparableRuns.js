// The Matrix reads only runs the results endpoint can line up row by row:
// runs that share one dataset version, trial plan and evaluators (the run
// record's `comparison_group`). Any other mix is refused ("mixed comparison
// is not supported"). The experiment page reads its newest such group (Run
// history, ExperimentRunHistory); the compare page keeps the baseline's
// group and names the runs it leaves out; the lineage ribbon's hop numbers
// read the runs of its path that line up. Pure.

/**
 * The group a run lines up in: its `comparison_group`; a record from before
 * the field keeps the old single group, and a run whose plan is unavailable
 * (null) stands alone. Run history (useExperimentRunHistory) groups runs
 * with it too.
 */
export const comparisonKeyOf = (run) =>
  run.comparison_group === undefined
    ? "legacy"
    : run.comparison_group ?? `run:${run.run_id}`;

/**
 * The runs of `runIds` in the baseline's group (else the first known run's),
 * in their order, and the runs left out. A run with no record stays: only
 * the server can tell, so its read decides.
 * @param {string[]} runIds
 * @param {{baselineId?: string|null, runs?: object[]}} opts  run records
 *   with `run_id` and `comparison_group` (R1 or the experiment's runs)
 * @returns {{runIds: string[], left: string[]}}
 */
export function comparableRunIds(runIds, { baselineId, runs } = {}) {
  const ids = runIds ?? [];
  const byId = new Map(
    (runs ?? []).filter((r) => r?.run_id).map((r) => [r.run_id, r]),
  );
  const anchorId =
    baselineId && byId.has(baselineId) && ids.includes(baselineId)
      ? baselineId
      : ids.find((id) => byId.has(id));
  if (!anchorId) return { runIds: ids, left: [] };
  const key = comparisonKeyOf(byId.get(anchorId));
  const left = ids.filter(
    (id) => byId.has(id) && comparisonKeyOf(byId.get(id)) !== key,
  );
  return { runIds: ids.filter((id) => !left.includes(id)), left };
}

/**
 * The runs of `runs` that don't line up with the runs shown: not among
 * them, and in another group than the first of them. [] when the first of
 * them has no record.
 * @param {string[]} shownIds
 * @param {object[]} runs  run records with `run_id` and `comparison_group`
 * @returns {string[]}
 */
export function otherGroupIds(shownIds, runs) {
  const ids = shownIds ?? [];
  const list = (runs ?? []).filter((r) => r?.run_id);
  const first = list.find((r) => r.run_id === ids[0]);
  if (!first) return [];
  const key = comparisonKeyOf(first);
  return list
    .filter((r) => !ids.includes(r.run_id) && comparisonKeyOf(r) !== key)
    .map((r) => r.run_id);
}

const listOf = (names) =>
  names.length < 2
    ? names.join("")
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

/**
 * One plain line when runs are left out of the matrix, naming the runs it
 * shows; null when none is left out.
 * `Showing #2 and #3. #1 is left out: it uses a different dataset version,
 * trial plan or evaluators.`
 * @param {string[]} runIds  the runs asked for, in order
 * @param {string[]} left  the runs left out
 * @param {(id: string) => string} labelOf
 */
export function leftOutLine(runIds, left, labelOf) {
  if (!left?.length) return null;
  const name = (id) => labelOf(id) || "a run";
  const shown = (runIds ?? []).filter((id) => !left.includes(id));
  if (shown.length < 2)
    return `${listOf(
      (runIds ?? []).map(name),
    )} can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Pick runs that share all three.`;
  const one = left.length === 1;
  return `Showing ${listOf(shown.map(name))}. ${listOf(left.map(name))} ${
    one ? "is" : "are"
  } left out: ${
    one
      ? "it uses a different dataset version, trial plan or evaluators."
      : "they use a different dataset version, trial plan or evaluators."
  }`;
}
