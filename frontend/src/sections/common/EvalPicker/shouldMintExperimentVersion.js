/**
 * Experiments mint versions via the template versions API (no dataset /
 * metric binding). Dataset hosts keep using edit_and_run_user_eval.
 */
export const shouldMintExperimentVersion = ({
  source,
  isDirty,
  isSystemEval,
}) =>
  source === "experiment" &&
  Boolean(isDirty) &&
  !isSystemEval;
