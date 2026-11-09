// One fix-run verdict (iterate r1 G3 P2; r2 G3 P2). Pure.
//  - A rerun of failing rows says what happened to the failure the fix
//    aimed at first: `Target failure fixed on 6 of 6 rows (order id keeps
//    "A-") · 7 other failures unchanged · 0 passing rows broke`.
//  - A full run reads `Full run of v3.1 on 30 rows: 6 better · 1 worse
//    (T13: 3/3 → 2/3, within noise) · 23 same` (never `Nothing to fix`).
// Rows that broke are always named (`brokeRows`, the banner links them).
// Tone: red only when something broke (beyond noise on a full run), green
// when the target is fixed and nothing broke, amber otherwise.
import { featuredPath } from "src/sections/common/Trajectory/pathModel";
import { fmtPct, fmtPts } from "src/sections/common/format/fmt";
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import { failedRowIds } from "./failedRule";

const rows = (n) => `${n} ${n === 1 ? "row" : "rows"}`;
const failures = (n) => `${n} other ${n === 1 ? "failure" : "failures"}`;
const MAX_NAMED = 2;

/** `T13` / `T13, T23` / `T13, T23 +2` (the first rows by name). */
export function namesOf(list, labelOf = (id) => id) {
  const names = list.slice(0, MAX_NAMED).map((id) => labelOf(id) ?? id);
  const more = list.length - names.length;
  return `${names.join(", ")}${more > 0 ? ` +${more}` : ""}`;
}

/**
 * The verdict of a rerun (the rows the fix ran itself).
 * @param {{fixed: string[], stillFailing: string[], newFailures: string[]}} s
 *   summarizeIteration output
 * @param {{target?: string[]|null, fixedText?: string|null,
 *   labelOf?: (id: string) => string, notRechecked?: number,
 *   noChange?: boolean, from?: string|null}} [opts]  `target` = the rows of
 *   the failure the fix aimed at (the finding's rows); none → every failing
 *   row. `noChange` = the rerun changed nothing (`Rerun as is`, never
 *   `Fixed`); `from` names the run it reran.
 * @returns {{word: string, tone: "good"|"mixed"|"bad"|"none", text: string,
 *   failing: number, fixed: number, still: number, broke: number,
 *   brokeRows: string[], targetFixed: number, targetTotal: number,
 *   others: number}}
 */
export function fixVerdict(s, opts = {}) {
  // iterate r3 G3 (P3): `notRechecked` = rows of the run the fix did not
  // execute (carried over). Then the line never says `0 passing rows broke`
  // (nothing was checked there): `6 of 6 target rows fixed · 24 rows not
  // re-checked`, amber until the other rows run.
  const {
    target,
    fixedText,
    labelOf,
    notRechecked = 0,
    noChange = false,
    from = null,
  } = opts;
  const unchecked = Number.isFinite(notRechecked) && notRechecked > 0;
  const fixedIds = s?.fixed ?? [];
  const stillIds = s?.stillFailing ?? [];
  const brokeRows = s?.newFailures ?? [];
  const fixed = fixedIds.length;
  const still = stillIds.length;
  const broke = brokeRows.length;
  const failing = fixed + still;
  // W1-9 B6 (review M4): rerun rows no score backs (summarizeIteration
  // `exclude`) are in no count; with none left there is no verdict.
  const unscoredIds = new Set(s?.unscored ?? []);
  const scoredRerun = (s?.rerun ?? []).filter(
    (id) => !unscoredIds.has(id),
  ).length;
  const leftOut = (s?.rerun?.length ?? 0) - scoredRerun;
  const brokeText = broke
    ? `${broke} passing ${broke === 1 ? "row" : "rows"} broke (${namesOf(brokeRows, labelOf)})`
    : unchecked
      ? `${rows(notRechecked)} not re-checked`
      : "0 passing rows broke";
  const base = { failing, fixed, still, broke, brokeRows, leftOut };
  if (leftOut > 0 && !scoredRerun)
    return {
      ...base,
      word: "Not scored",
      tone: "none",
      text: `No verdict: none of the ${rows(leftOut)} ${from ? `rerun from ${from} ` : "rerun "}has a score`,
      targetFixed: 0,
      targetTotal: 0,
      others: 0,
    };
  // options-truth L3 G10 (banner:verdict): a rerun with nothing changed
  // fixes nothing. Rows that pass now passed by chance (their tries
  // resampled), so the line says what happened without `Fixed N of M`.
  if (noChange) {
    const flipped = broke
      ? `${broke} passing ${broke === 1 ? "row" : "rows"} failed this time (${namesOf(brokeRows, labelOf)})`
      : null;
    return {
      ...base,
      fixed: 0,
      passedNow: fixed,
      word: "Rerun as is",
      tone: "none",
      text: [
        `Nothing changed${from ? ` from ${from}` : ""}`,
        failing
          ? `${fixed} of ${failing} failing ${failing === 1 ? "row" : "rows"} passed this time`
          : `All ${rows(scoredRerun)} still pass`,
        ...(still ? [`${still} still fail`] : []),
        ...(flipped ? [flipped] : []),
        ...(unchecked ? [`${rows(notRechecked)} not re-checked`] : []),
      ].join(" · "),
      targetFixed: 0,
      targetTotal: failing,
      others: 0,
    };
  }
  const aimed = Array.isArray(target) && target.length ? new Set(target) : null;
  if (!failing) {
    return {
      ...base,
      word: broke ? "Broke rows" : "No failures",
      tone: broke ? "bad" : "good",
      text: broke
        ? `None of the rerun rows was failing · ${brokeText}`
        : `All ${rows(scoredRerun)} still pass · 0 passing rows broke`,
      targetFixed: 0,
      targetTotal: 0,
      others: 0,
    };
  }
  if (aimed) {
    const tFixed = fixedIds.filter((id) => aimed.has(id)).length;
    const tStill = stillIds.filter((id) => aimed.has(id)).length;
    const tTotal = tFixed + tStill;
    const othersStill = stillIds.filter((id) => !aimed.has(id)).length;
    const othersFixed = fixedIds.filter((id) => !aimed.has(id)).length;
    const all = tTotal > 0 && tStill === 0;
    const head = tTotal
      ? unchecked
        ? `${tFixed} of ${tTotal} target ${tTotal === 1 ? "row" : "rows"} fixed${all && fixedText ? ` (${fixedText})` : ""}`
        : `Target failure fixed on ${tFixed} of ${rows(tTotal)}${all && fixedText ? ` (${fixedText})` : ""}`
      : "Target failure not among the rerun rows";
    const parts = [head];
    if (tStill) parts.push(`${tStill} still fail`);
    if (othersStill) parts.push(`${failures(othersStill)} unchanged`);
    if (othersFixed) parts.push(`${othersFixed} other fixed too`);
    parts.push(brokeText);
    return {
      ...base,
      word: all ? "Fixed" : tFixed ? "Partly fixed" : "Not fixed yet",
      tone: broke ? "bad" : all && !unchecked ? "good" : "mixed",
      text: parts.join(" · "),
      targetFixed: tFixed,
      targetTotal: tTotal,
      others: othersStill,
    };
  }
  const all = still === 0;
  return {
    ...base,
    word: all ? "Fixed" : fixed ? "Partly fixed" : "Not fixed yet",
    tone: broke ? "bad" : all && !unchecked ? "good" : "mixed",
    text: [
      all
        ? `Fixed: all ${rows(failing)} that failed now pass`
        : `Fixed ${fixed} of ${failing} failing ${failing === 1 ? "row" : "rows"}`,
      ...(all ? [] : [`${still} still fail`]),
      brokeText,
    ].join(" · "),
    targetFixed: fixed,
    targetTotal: failing,
    others: 0,
  };
}

/** `3/3` from a matrix cell (passing tries / tries), or `—`. */
export function triesText(cell) {
  const t = Array.isArray(cell?.trials) ? cell.trials : [];
  if (!t.length) return "—";
  return `${t.filter((x) => x === 1).length}/${t.length}`;
}

/**
 * The verdict of a full run (every row, compared with the baseline).
 * @param {object} cmp  runNumbers.compareRuns over every row (S5); W1-9 B6
 *   (review M4): over the rows a score backs when some are left out
 * @param {{name: string, noise: string, labelOf?: (id) => string,
 *   movesOf?: (id) => string|null, total?: number, leftOut?: number}} opts
 *   `name` = what ran (`v3.1`), `noise` = `within noise` / `significant`,
 *   `movesOf(id)` = `3/3 → 2/3`, `leftOut` = rows no score backs (in no
 *   count; the unscored line names them)
 * @returns {{word: string, tone: string, text: string, worseRows: string[]}}
 */
export function fullRunVerdict(
  cmp,
  { name, noise, labelOf, movesOf, total, leftOut = 0 },
) {
  const worseRows = cmp?.worseIds ?? [];
  const worse = cmp?.worse ?? 0;
  const better = cmp?.better ?? 0;
  const same = cmp?.same ?? 0;
  const n = total ?? better + worse + same + (cmp?.notRun ?? 0) + leftOut;
  // No row has a score in both runs: no verdict. The short line keeps the
  // banner's unscored line (why, and how many rows) visible beside it.
  if (leftOut > 0 && !(better + worse + same))
    return {
      word: "Not scored",
      tone: "none",
      text: `Full run of ${name} on ${rows(n)}: no verdict`,
      worseRows: [],
    };
  const named = worseRows
    .slice(0, MAX_NAMED)
    .map((id) => {
      const mv = movesOf?.(id);
      return `${labelOf?.(id) ?? id}${mv ? `: ${mv}` : ""}`;
    })
    .join(", ");
  const more = worseRows.length - Math.min(worseRows.length, MAX_NAMED);
  const worseText = worse
    ? `${worse} worse (${named}${more > 0 ? ` +${more}` : ""}${noise ? `, ${noise}` : ""})`
    : "0 worse";
  const significant = worse > 0 && noise === "significant";
  return {
    word: worse ? (significant ? "Worse rows" : "Mostly better") : "Better",
    tone: significant ? "bad" : worse ? "mixed" : better ? "good" : "none",
    text: `Full run of ${name} on ${rows(n)}: ${better} better · ${worseText} · ${same} same`,
    worseRows,
  };
}

/**
 * `65.6% → 67.8% (+2.2 pts)` from a compareRuns result (rates as shown,
 * pts = its `deltaPts`), or null.
 */
export function successMove(cmp) {
  const from = cmp?.baseline?.rate;
  const to = cmp?.candidate?.rate;
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  return `${fmtPct(from)} → ${fmtPct(to)} (${fmtPts(cmp.deltaPts)})`;
}

/**
 * Failing tries on one path, before → after: the fixed path (`pathId`), else
 * the base run's path that fails most. `after` is 0 when the new run never
 * takes it.
 * @returns {{path: object, from: number, to: number}|null}
 */
export function pathFailMove(baseAgg, candAgg, pathId) {
  const base =
    (pathId && (baseAgg?.paths ?? []).find((p) => p.id === pathId)) ||
    featuredPath(baseAgg);
  if (!base || !candAgg) return null;
  const cand = (candAgg.paths ?? []).find((p) => p.id === base.id);
  return { path: base, from: base.failed ?? 0, to: cand?.failed ?? 0 };
}

/**
 * options-truth L3 G10 (banner:verdict): true when the paths aggregate has
 * at least one path. A run with no traces (`{paths: []}`) has no failing
 * path to name, so the banner prints no `Most common failing path` line.
 */
export function hasPathData(agg) {
  return Array.isArray(agg?.paths) && agg.paths.length > 0;
}

/**
 * options-truth L3 G10 (banner:verdict): true when every run from the
 * candidate back to the base (by `parent_run_id`) was rerun with an
 * explicit empty `changes` list, so nothing changed and nothing can be
 * `Fixed`. Unknown history (no `changes` field, a broken chain) is false.
 * @param {object[]} runs  R1 results (`run_id`, `parent_run_id`, `changes`)
 */
export function ranAsIs(runs, candId, baseId) {
  const byId = new Map((runs ?? []).map((r) => [r.run_id, r]));
  let cur = byId.get(candId);
  let hops = 0;
  while (cur && cur.run_id !== baseId) {
    if (!Array.isArray(cur.changes) || cur.changes.length) return false;
    hops += 1;
    if (hops > byId.size) return false;
    cur = byId.get(cur.parent_run_id);
  }
  return Boolean(cur) && hops > 0;
}

/**
 * options-truth L3 G10 (banner:verdict): true while any of the new run's
 * tries in the rerun scope is still unscored, so the banner keeps polling
 * until its verdict can be read. A missing matrix is not pending.
 * @param {object|null|undefined} matrix  the pair's matrix (R3 shape)
 */
export function candidatePending(matrix, baseId, candId) {
  if (!matrix || !candId) return false;
  return Boolean(
    compareRuns({
      matrix,
      baselineId: baseId,
      candidateId: candId,
      scope: "rerun",
    })?.partial,
  );
}

/**
 * options-truth L3 G10 (banner:verdict): the candidate's paths can back a
 * `failing tries → N` move only when they record its failures: a run with
 * rows that still fail but no failed try on any path has no failing-path
 * data (its traces were not aggregated by outcome), so no line is printed.
 * @param {{paths?: {failed?: number}[]}|null} candAgg
 * @param {number} stillFailing  rows of the new run that still fail
 */
export function failurePathsKnown(candAgg, stillFailing) {
  if (!hasPathData(candAgg)) return false;
  if (!(stillFailing > 0)) return true;
  return candAgg.paths.some((p) => (p?.failed ?? 0) > 0);
}

/**
 * W1-9 B6: the Failed rows of both runs on the rows both ran, by the one
 * Failed rule (failedRule.js) the dock's `Failed (13)` chip and
 * summarizeIteration read, so the banner's `Fixed 6 of 13 failing rows ·
 * 7 still fail` equals the Failed counts on the parent and the child.
 * `scope: "rerun"` = only rows the child ran itself (carried rows are the
 * parent's results).
 * @returns {{base: string[], cand: string[]}}
 */
export function failedRowsOf(matrix, baseId, candId, { scope = "all" } = {}) {
  const only = (r) =>
    Boolean(r.cells?.[baseId] && r.cells?.[candId]) &&
    (scope !== "rerun" || !r.cells[candId].carried_from);
  return {
    base: failedRowIds(matrix, baseId, { only }),
    cand: failedRowIds(matrix, candId, { only }),
  };
}

/**
 * W1-9 B6 (review M4): the compareRuns scope of a verdict that leaves rows
 * out: every matrix row but those (`"all"` when none is left out), so the
 * full run's better / worse / same and its success move read only rows a
 * score backs.
 * @param {{rows?: object[]}} matrix
 * @param {string[]} leftOut  verdictLeftOut row ids
 * @returns {"all"|string[]}
 */
export function scoredScope(matrix, leftOut) {
  if (!leftOut?.length) return "all";
  const out = new Set(leftOut);
  return (matrix?.rows ?? []).map((r) => r.row_id).filter((id) => !out.has(id));
}
