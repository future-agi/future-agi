// The side-by-side door for one run. Pure; the hover actions, the row menu
// and the right-click menu share it.
//
// IT-R4 G2 P1 (R2): a run pairs with the dataset's PINNED baseline whenever
// there is one, it is not this run and it puts out the same kind of thing
// (text with text, images with images). Else it pairs with the previous
// sibling of its own experiment (#7B ↔ #7A; the first run pairs with the
// next one), and only then with the view's baseline. Only the pinned
// baseline is passed as `baselineId`: a sibling is a plain lane, never
// labelled Baseline (rule 30). `Compare with…` (RowHoverActions) picks any
// other run.
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { outputModalityOf, sameOutput } from "src/sections/common/Runs/runKind";

const letterOf = (run) =>
  String(run?.label ?? "").match(/^#\d+([A-Z]+)/)?.[1] ?? "";

/** The dataset's pinned baseline run (object), or null. */
export function pinnedBaselineOf(context) {
  const id = context?.pinnedRunId;
  if (!id) return null;
  if (context?.baselineRun?.run_id === id) return context.baselineRun;
  return (context?.allRuns ?? []).find((r) => r.run_id === id) ?? null;
}

function siblingOf(run, context) {
  const sibs = (context?.allRuns ?? [])
    .filter(
      (r) =>
        r.experiment_id === run.experiment_id &&
        r.run_id !== run.run_id &&
        r.listed !== false,
    )
    .sort((a, b) => letterOf(a).localeCompare(letterOf(b)));
  if (!sibs.length) return null;
  const me = letterOf(run);
  const before = sibs.filter((r) => letterOf(r).localeCompare(me) < 0);
  return before.length
    ? { run: before[before.length - 1], role: "previous" }
    : { run: sibs[0], role: "next" };
}

/**
 * The pair for `run` with its role: `baseline` (the pinned baseline),
 * `previous` / `next` (a sibling of its experiment) or `other` (the view's
 * baseline when the run has no sibling). Null when there is none.
 * @returns {{run: object, role: "baseline"|"previous"|"next"|"other"}|null}
 */
export function sideBySidePair(run, context) {
  if (!run) return null;
  const pinned = pinnedBaselineOf(context);
  if (pinned && pinned.run_id !== run.run_id && sameOutput(run, pinned))
    return { run: pinned, role: "baseline" };
  const sib = siblingOf(run, context);
  if (sib) return sib;
  const base =
    context?.baselineRun ??
    (context?.baselineId ? { run_id: context.baselineId } : null);
  if (base && base.run_id !== run.run_id)
    return {
      run: base,
      role: base.run_id === pinned?.run_id ? "baseline" : "other",
    };
  return null;
}

/** The run `run` is compared with side by side, or null. */
export function sideBySidePartner(run, context) {
  return sideBySidePair(run, context)?.run ?? null;
}

/**
 * The door's hover words: `Side by side with baseline #1A` /
 * `Side by side with #7A (previous run)`.
 */
export function sideBySideTitle(run, context, partner) {
  const own = sideBySidePair(run, context);
  const pair =
    !partner || partner.run_id === own?.run?.run_id
      ? own
      : {
          run: partner,
          role: partner.run_id === context?.pinnedRunId ? "baseline" : "other",
        };
  if (!pair) return null;
  const label = pair.run.label ?? "the other run";
  if (pair.role === "baseline") return `Side by side with baseline ${label}`;
  if (pair.role === "previous")
    return `Side by side with ${label} (previous run)`;
  if (pair.role === "next") return `Side by side with ${label} (next run)`;
  return `Side by side with ${label}`;
}

/** IT-R2 G2 P6: an image-output run is compared on its pictures. */
export const comparesImages = (run) => outputModalityOf(run) === "image";

/** The door's words: `Compare images` for image output, else `Side by side`. */
export const sideBySideLabel = (run) =>
  comparesImages(run) ? "Compare images" : "Side by side";

/**
 * MUST 5: the side-by-side door for one run (its own experiment first).
 * Image output opens on the Answers tab (both pictures, side by side).
 */
export function sideBySideHref(run, context, partner) {
  const other = partner ?? sideBySidePartner(run, context);
  if (!run || !other) return null;
  const href = taskCompareHref({
    datasetId: context.datasetId,
    runIds: [run.run_id, other.run_id],
    // IT-R4 G2 P1: only the pinned baseline is a Baseline lane.
    baselineId:
      other.run_id === context?.pinnedRunId ? other.run_id : undefined,
    from: context.fromUrl,
  });
  return comparesImages(run) && comparesImages(other)
    ? `${href}${href.includes("?") ? "&" : "?"}inspect=answers`
    : href;
}

export const sideBySideDisabledReason = (run, context) =>
  sideBySidePartner(run, context)
    ? null
    : !context?.baselineId
      ? "Pick a baseline first: side by side compares a run with it"
      : "This is the baseline. Hover another run to compare it with this one";
