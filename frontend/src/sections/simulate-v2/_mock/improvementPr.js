/*
  The pull request an improvement opened. Improvement ids (OPT-00002) count
  up per environment, so two environments both have an OPT-00002 — a PR is
  matched on its environment as well as its improvement, or one environment's
  improvement would show another's PR. A PR recorded without an environment
  still matches on its id alone.
*/
export const prOfImprovement = (prs, optId, envId, { open = false } = {}) => (prs || []).find((p) => {
  if ((p.handoffKey || p.meta?.handoffKey) !== optId) return false;
  const prEnv = p.envId || p.meta?.envId || null;
  if (envId && prEnv && prEnv !== envId) return false;
  return !open || p.status !== "closed";
}) || null;
