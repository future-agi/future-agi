/*
  One "Conversations at once" setting for every run. A run on this page takes
  up to RUN_LIMITS.maxParallel of them (parallelOf); a batch — a run over 200
  conversations, in the background — takes the full number. Saved as
  runDefaults.batchParallel; with nothing saved, one at a time when parallel
  runs are off, else 50.
*/
export const AT_ONCE_OPTIONS = [1, 2, 3, 4, 10, 25, 50, 100];

export const atOnceOf = (envState) => {
  const saved = Math.round(Number(envState?.runDefaults?.batchParallel));
  if (Number.isFinite(saved) && saved >= 1) return saved;
  return envState?.parallelRuns === false ? 1 : 50;
};
