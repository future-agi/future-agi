// Detail responses retain raw eval_scores for legacy consumers. When only the
// additive rollup is available, recover the row's own spans rather than every
// descendant rollup entry; callers that recurse can then aggregate exactly
// once.
export const getOwnEvalScores = (entry) => {
  if (Array.isArray(entry?.eval_scores)) return entry.eval_scores;

  const spanId = entry?.observation_span?.id;
  const rollupEvals = entry?.eval_rollup?.evals;
  if (!spanId || !Array.isArray(rollupEvals)) return [];

  return rollupEvals.flatMap((evalEntry) =>
    (evalEntry?.spans || [])
      .filter((span) => String(span?.span_id) === String(spanId))
      .map((span) => {
        const value = span?.value;
        const isPassFail = value === "pass" || value === "fail";
        const isChoiceList = Array.isArray(value);
        return {
          ...span,
          // Timeline and graph consumers predate eval_rollup and read the
          // legacy score/result fields. Keep the additive fallback compatible
          // with those consumers without changing the rollup itself.
          score: isPassFail
            ? value === "pass"
              ? 100
              : 0
            : typeof value === "number"
              ? value
              : null,
          result: isPassFail
            ? value === "pass"
            : isChoiceList
              ? value
              : value ?? null,
          score_items: isChoiceList ? value : null,
          eval_config_id: evalEntry.eval_config_id,
          eval_name: evalEntry.eval_name,
        };
      }),
  );
};

/**
 * W1-9: an eval entry's verdict: its boolean `result` when it has one (the
 * scorer's own pass rule, e.g. a 0.65 under a 0.7 threshold fails), else
 * `score >= 50`; null when it has neither (skipped, N/A, score error).
 */
export const evalPassOf = (e) => {
  if (e?.result === true || e?.result === false) return e.result;
  const s = typeof e?.score === "number" ? e.score : null;
  return s == null ? null : s >= 50;
};

/** W1-9: the word for an entry with no verdict (`N/A`, `Score error` …). */
export const evalStatusWord = (e) => {
  if (e?.error === true || e?.status === "error") return "Score error";
  if (e?.status === "na") return "N/A";
  if (e?.status === "not_reached") return "Not reached";
  if (e?.status === "skipped" || e?.skipped_reason) return "Skipped";
  return "—";
};

/**
 * Pass / fail verdicts of a span subtree (recursive), for the root's
 * `2/6` roll-up. W1-9 (review S12): a verdict that never ran (skipped,
 * N/A, not reached, a score error) is neither and stays out of the count,
 * so 2 failures beside 4 checks never reached read `0/2`, not `0/6`.
 * @returns {{pass: number, fail: number, total: number}}
 */
export function collectSubtreeEvals(entry) {
  let pass = 0;
  let fail = 0;
  getOwnEvalScores(entry).forEach((e) => {
    const verdict = evalPassOf(e);
    if (verdict === true) pass += 1;
    else if (verdict === false) fail += 1;
  });
  (entry?.children ?? []).forEach((child) => {
    const sub = collectSubtreeEvals(child);
    pass += sub.pass;
    fail += sub.fail;
  });
  return { pass, fail, total: pass + fail };
}
