// Which rows the results grid shows (`show=`): All, or the rows where a run
// got worse, better or changed against the baseline (Worse, Better,
// Changed). Those three compare every other run in view with the baseline,
// so they need a baseline and another run to compare with it; the read
// refuses them otherwise.

export const MOVEMENT_SHOWS = Object.freeze([
  "regressed",
  "improved",
  "changed",
]);

/** Why Worse, Better and Changed are off: the view has no baseline. */
export const NO_BASELINE_LINE =
  "Pick a baseline run to compare the other runs with";

/** Why Worse, Better and Changed are off: the baseline is the only run. */
export const ONLY_BASELINE_LINE =
  "Add another run to compare with the baseline";

/**
 * The line that says why the view cannot compare its runs with a baseline,
 * or null when it can: a baseline is set (not `none`) and some run in view
 * is not the baseline. Pure.
 * @param {{runIds?: string[], baselineId?: string|null}} view
 */
export function noBaselineLine({ runIds = [], baselineId } = {}) {
  if (!baselineId || baselineId === "none") return NO_BASELINE_LINE;
  if (!(runIds ?? []).some((id) => id && id !== baselineId))
    return ONLY_BASELINE_LINE;
  return null;
}

/**
 * The show a view reads from its link's `show`, for its read and its
 * toolbar alike. One run reads as All. Worse, Better and Changed read as
 * All while the view has no baseline to compare with (noBaselineLine). The
 * link keeps its `show`, so it applies again once a baseline is back. Pure.
 * @param {string|null|undefined} show
 * @param {{runIds?: string[], baselineId?: string|null}} view
 */
export function showForView(show, { runIds = [], baselineId } = {}) {
  if (!show || show === "all" || (runIds ?? []).length < 2) return "all";
  if (MOVEMENT_SHOWS.includes(show) && noBaselineLine({ runIds, baselineId }))
    return "all";
  return show;
}
