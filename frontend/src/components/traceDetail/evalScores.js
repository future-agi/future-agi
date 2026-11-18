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
