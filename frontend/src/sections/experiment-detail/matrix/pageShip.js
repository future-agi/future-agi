// IT-R4 G6 P1 (rule 27, S1, S2): one candidate, one baseline and one ship
// line per URL. Matrix, Summary and the compare header read the page
// candidate (`cand=`, else the Experiments headline's pick) and build the
// ship line only through G2's `leaderboardHeadline.shipLine`. Pure.
import {
  pickHeadline,
  shipLine,
} from "src/sections/develop-detail/ExperimentTab/leaderboardHeadline";
import { rowLabelOf } from "src/sections/common/Runs/runNumbers";
import { runSpokenName } from "src/sections/common/Runs/runLabel";
import { fmtCount, fmtPct } from "src/sections/common/format/fmt";
import { pageCandidate } from "../results/pageCandidate";

/** `Claude Code (#2D)`; the bare label when the run has no other name. */
export function whoText(run, label) {
  const l = label ?? run?.label ?? "";
  const name = run ? runSpokenName(run) : "";
  return name && name !== l ? `${name} (${l})` : l;
}

/**
 * The page candidate: a valid `cand=` › the Experiments headline's pick
 * (`pickHeadline`, the newest experiment's best finished run) › the
 * results pick (`pageCandidate`). Never "the run with the most worse tasks".
 * @param {{cand?: string|null, runIds: string[], baselineId: string|null,
 *   runs: object[]}} a  `runs` = any superset of the runs in view
 * @returns {string|null}
 */
export function pageCandidateId({ cand, runIds, baselineId, runs }) {
  const ids = runIds ?? [];
  const ok = (id) => Boolean(id) && id !== baselineId && ids.includes(id);
  if (ok(cand)) return cand;
  const inView = (runs ?? []).filter((r) => ids.includes(r?.run_id));
  const base = inView.find((r) => r.run_id === baselineId) ?? null;
  let picked = null;
  try {
    picked = pickHeadline(inView, base)?.candidate?.run_id ?? null;
  } catch {
    picked = null;
  }
  if (ok(picked)) return picked;
  return pageCandidate({ runs: inView, runIds: ids, baselineId }).id ?? null;
}

/** Rows of a compare the candidate did not run. */
const skippedByCandidate = (compare) =>
  (compare?.rows ?? []).filter(
    (r) => r.missing === "candidate" || r.missing === "both",
  ).length;

/**
 * The ship line of one compare, through G2's `shipLine`:
 * `Don't ship Claude Code (#2D) yet: it fails T25, which Support Agent v3
 * (#2B) passes.` Null while the pair is partial or nothing shippable.
 * @param {object} compare  runNumbers.compareRuns(...)
 * @param {{cand: string, base: string, rowLabel?: (n) => string,
 *   datasetKind?: string, noun?: string}} o
 */
export function pageShipLine(compare, o = {}) {
  if (!compare) return null;
  const partial = skippedByCandidate(compare) > 0;
  return shipLine(compare, {
    cand: o.cand,
    base: o.base,
    rowLabel: o.rowLabel ?? ((n) => rowLabelOf(n, o.datasetKind)),
    noun: o.noun ?? "task",
    partial,
    total: compare.rows?.length ?? null,
  });
}

/**
 * S1 (IT-R5 G6 P2, rule 37): the not-significant sentence, the same string
 * as the Experiments tab: `Too close to call: Claude Code (#2D) and Support
 * Agent v3 (#2B) pass 91.0% vs 65.6% of 30 tasks (95% range −2 to +12
 * pts).` The range is runNumbers.diffRange95's (rule 36: one range per
 * pair); pass it, or `{lo, hi}` in pts, or nothing.
 */
export function levelSentence({ cand, base, a, b, n, noun = "task", range }) {
  const text =
    range?.text ??
    (range && Number.isFinite(range.lo) && Number.isFinite(range.hi)
      ? `95% range ${fmtRangePts(range.lo)} to ${fmtRangePts(range.hi)} pts`
      : null);
  return `Too close to call: ${cand} and ${base} pass ${fmtPct(a)} vs ${fmtPct(b)} of ${fmtCount(n, noun)}${text ? ` (${text})` : ""}.`;
}

const MINUS = "−";
// Whole pts with a sign, as diffRange95 prints them (`−12`, `+36`, `0`).
const fmtRangePts = (v) => {
  const r = Math.round(v);
  return r > 0 ? `+${r}` : r < 0 ? `${MINUS}${Math.abs(r)}` : "0";
};

/**
 * Header chip population (rule 28): `91.0% · 81 of 89 scored tries (1 not
 * scored)`; `91.0% · 81 of 90 tries` when every try was scored.
 * @param {{rate: number, passed: number, scored: number, total?: number}} s
 */
export function populationText({ rate, passed, scored, total }) {
  if (!Number.isFinite(rate) || !Number.isFinite(scored)) return null;
  const unscored =
    Number.isFinite(total) && total > scored ? total - scored : 0;
  return unscored
    ? `${fmtPct(rate)} · ${passed} of ${scored} scored tries (${unscored} not scored)`
    : `${fmtPct(rate)} · ${passed} of ${fmtCount(scored, "try")}`;
}
