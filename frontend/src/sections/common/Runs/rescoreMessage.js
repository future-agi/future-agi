// W1-2: what a re-score did, in one sentence: the mock's server message
// and every door's toast (rescoreCopyModel re-exports it). Pure.
import {
  joinAnd,
  keepScores,
  n,
  plural,
  rescoreResultOf,
  skippedText,
} from "./rescoreWords";

/**
 * S4: the versions an unchanged re-score read, per eval: `Correctness v3`,
 * `Correctness v2 and v3` (two runs on two versions); null when the server
 * named none per eval.
 */
function versionsText(evalVersions) {
  const list = (evalVersions ?? []).filter((e) => e?.versions?.length);
  if (!list.length) return null;
  return list.map((e) => `${e.name} ${joinAnd(e.versions)}`).join(" · ");
}

/**
 * M5: how the flipped verdicts read. A verdict is one eval on one try of
 * one run, so cells outnumber rows when several evals, runs or tries
 * flipped; the sentence then names the unit and the reason:
 * - `199 rows changed (199 Pass → Fail)` (one eval, one run, one try)
 * - `253 scores changed on 208 rows across 2 evals (242 Pass → Fail, 11
 *   Fail → Pass)`
 * - `400 scores changed on 217 rows across 2 runs (400 Pass → Fail)`
 */
function changedText(r) {
  const runs = r.runs ?? [];
  const rows = new Set(runs.flatMap((x) => x.flipped_rows ?? [])).size;
  if (!rows) return null;
  const f2p = r.fail_to_pass ?? 0;
  const p2f = r.pass_to_fail ?? 0;
  const dirs = [
    p2f ? `${n(p2f)} Pass → Fail` : null,
    f2p ? `${n(f2p)} Fail → Pass` : null,
  ].filter(Boolean);
  const cells = f2p + p2f;
  if (cells === rows)
    return `${plural(rows, "row")} changed (${dirs.join(", ")})`;
  const evals = new Set(runs.flatMap((x) => x.flipped_eval_keys ?? [])).size;
  const flippedRuns = runs.filter((x) => (x.flipped_rows ?? []).length).length;
  const reasons = [
    evals > 1 ? `${evals} evals` : null,
    flippedRuns > 1 ? `${flippedRuns} runs` : null,
    runs.some((x) => x.flipped_on_several_tries) ? "tries" : null,
  ].filter(Boolean);
  const across = reasons.length ? ` across ${joinAnd(reasons)}` : "";
  return `${plural(cells, "score")} changed on ${plural(rows, "row")}${across} (${dirs.join(", ")})`;
}

/**
 * Finalize r1-r2: scores that held a verdict and now hold none, with why:
 * `19 scores not judged, no TypeSafe key (403)`. A re-score whose judge was
 * refused on every row must not read "no verdict changed".
 */
function unjudgedText(r) {
  const reasons = new Map();
  (r.runs ?? []).forEach((x) =>
    Object.entries(x.unjudged_reasons ?? {}).forEach(([why, k]) =>
      reasons.set(why, (reasons.get(why) ?? 0) + k),
    ),
  );
  const total = [...reasons.values()].reduce((t, k) => t + k, 0);
  if (!total) return null;
  return `${plural(total, "score")} not judged, ${joinAnd([...reasons.keys()])}`;
}

/**
 * What a re-score did, in one sentence (the server's message and every
 * door's toast):
 * - `Re-scored #2A with Tone: 7 rows changed (5 Pass → Fail, 2 Fail → Pass).
 *   #2A keeps its scores.`
 * - `Scored 7 rows of #2A with Correctness in #2A·2. #2A keeps its scores.`
 * - `Scores unchanged · same eval version (Correctness v2)`
 */
export function rescoreMessage(result) {
  const r = rescoreResultOf(result);
  if (!r) return "";
  if (r.unchanged) {
    if ((r.unchanged_labels ?? []).length === 0 && (r.skipped ?? []).length)
      return `Nothing re-scored.${skippedText(r.skipped)}`;
    const per = versionsText(r.eval_versions);
    const several = (r.eval_versions ?? []).filter(
      (e) => e?.versions?.length,
    ).length;
    const head = r.same_definition
      ? per
        ? `Scores unchanged · same eval ${several > 1 ? "versions" : "version"} (${per})`
        : `Scores unchanged · same eval version (${r.eval_version ?? "v1"})`
      : per
        ? `Scores unchanged · no score or verdict moved, so no new run (${per})`
        : `Scores unchanged · no score or verdict moved, so no new run (eval ${r.eval_version ?? "v1"})`;
    return `${head}${skippedText(r.skipped)}`;
  }
  const runs = r.runs ?? [];
  const from = runs.map((x) => x.from_label).filter(Boolean);
  const names = joinAnd(r.eval_names ?? []);
  const scoped = runs.length > 0 && runs.every((x) => x.row_scoped);
  const changed = changedText(r);
  const unjudged = unjudgedText(r);
  const moved =
    [changed, unjudged].filter(Boolean).join("; ") || "no verdict changed";
  // W1-7: only the error policy moved.
  const policyOnly =
    runs.length > 0 &&
    runs.every((x) => x.error_policy) &&
    !changed &&
    !unjudged;
  if (policyOnly) {
    const nErr = runs.reduce((sum, x) => sum + (x.errored_tries ?? 0), 0);
    const p = runs[0].error_policy;
    const what =
      p === "exclude"
        ? "left out of every score"
        : p === "fail"
          ? "count as 0 and fail the run"
          : "count as 0";
    return `Re-scored ${joinAnd(from)} in ${joinAnd(r.labels ?? [])}: ${plural(nErr, "errored try", "errored tries")} ${what}. ${keepScores(from)}`;
  }
  // Evals no run carried before are scored for the first time: no flips.
  const firstTime =
    runs.length > 0 &&
    runs.every(
      (x) =>
        (x.new_eval_keys ?? []).length > 0 &&
        (x.new_eval_keys ?? []).length === (x.eval_keys ?? []).length,
    );
  const labels = joinAnd(r.labels ?? []);
  const lead = firstTime
    ? scoped
      ? `Scored ${plural(runs[0].rows ?? 0, "row")} of ${joinAnd(from)} with ${names} in ${labels}.`
      : `Scored ${joinAnd(from)} with ${names} in ${labels}.`
    : scoped
      ? `Scored ${plural(runs[0].rows ?? 0, "row")} of ${joinAnd(from)} with ${names} in ${labels}: ${moved}.`
      : `Re-scored ${joinAnd(from)} with ${names}: ${moved}.`;
  const same = (r.unchanged_labels ?? []).length
    ? ` ${joinAnd(r.unchanged_labels)} unchanged.`
    : "";
  return `${lead} ${keepScores(from)}${same}${skippedText(r.skipped)}`;
}
