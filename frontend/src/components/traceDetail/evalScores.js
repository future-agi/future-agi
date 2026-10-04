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
      .map((span) => ({
        ...span,
        eval_config_id: evalEntry.eval_config_id,
        eval_name: evalEntry.eval_name,
      })),
  );
};
