// dataset integration FE-0 (owner Q12) on the experiment header's Rerun menu: only the
// runs Future AGI can start again itself (prompt, agent graph, endpoint) are
// offered. A code, harness, voice or uploaded run came from a generated
// path or a live connection that does not exist, so the menu leaves it out
// instead of offering a door that ends nowhere (the leaderboard, the run
// menus and the Test a fix dock already follow `canRerunHere` / `canIterate`).
//
// The experiment detail's run list carries no kind; the dataset's run
// configs do. Reading `canRerunHere` on the bare list called every run a
// prompt run, so TS #3 (six code and harness runs) offered `Rerun with
// changes… Start from #3F` and `All tasks, all runs` with an untracked cost.
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { fmtCount } from "src/sections/common/format/fmt";

/**
 * The experiment's runs with their configs, the ones Future AGI can rerun,
 * and the run `Rerun with changes…` starts from.
 * @param {object[]} runs        the experiment detail's runs
 * @param {object[][]} sources   run lists that carry `config` (the first list
 *   that has a run's config wins over the run's own)
 * @param {string|null} [cand]   the page's candidate run id
 * @returns {{runs: object[], here: object[], allHere: boolean,
 *   changesRun: object|null}}
 */
export function rerunHereOf(runs = [], sources = [], cand = null) {
  const configs = new Map();
  for (const list of sources ?? [])
    for (const r of list ?? [])
      if (r?.run_id && r.config && !configs.has(r.run_id))
        configs.set(r.run_id, r.config);
  const withConfig = (runs ?? []).map((r) =>
    configs.has(r.run_id) ? { ...r, config: configs.get(r.run_id) } : r,
  );
  const here = withConfig.filter(canRerunHere);
  return {
    runs: withConfig,
    here,
    allHere: here.length > 0 && here.length === withConfig.length,
    changesRun:
      here.find((r) => r.run_id === cand) ?? here[here.length - 1] ?? null,
  };
}

/**
 * The menu's `All …` item: `All tasks, all runs`, or the runs it reruns by
 * label when some runs cannot be started again here (`All rows, #2A, #2C`).
 * @param {{here: object[], allHere: boolean}} plan  from rerunHereOf
 * @param {string} [noun]
 */
export function allItemLabel(
  { here = [], allHere = false } = {},
  noun = "row",
) {
  if (allHere) return `All ${noun}s, all runs`;
  const names = here.map((r) => r.label).filter(Boolean);
  return `All ${noun}s, ${names.length ? names.join(", ") : fmtCount(here.length, "run")}`;
}
