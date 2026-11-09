// W6 A1 (review M1): a Try on one row is the run's try on that row, so it
// carries the run's row scope: the Data step's splits and chosen rows. The
// mock run engine gives a Recorded tool its recordings on the first rows of
// that scope (toolFixtures.toolRecordingsOf, the create path's one rule),
// and a Try sent without the scope would key a different profile.
//
//   config.run_scope = {split_ids: string[], row_ids: string[]}
//
// Pure. TryOnOneRow sends `withRunScope(config, form)` (integration line) and
// keys its result on it, so changing the split marks the result stale.

const ids = (list) =>
  (Array.isArray(list) ? list : []).filter(
    (v) => typeof v === "string" && v.length > 0,
  );

/**
 * The quick-test config with the run's row scope (`run_scope`), or the
 * config itself when the Data step narrows nothing (every row).
 * @param {object|null} config  cardRunConfig(card)
 * @param {{splitIds?: string[], rowIds?: string[]}} [form]  the experiment
 *   form context (RunExperiment: the Data step's splitIds / rowIds)
 */
export function withRunScope(config, form = {}) {
  if (!config || typeof config !== "object") return config ?? null;
  const split_ids = ids(form?.splitIds);
  const row_ids = ids(form?.rowIds);
  if (!split_ids.length && !row_ids.length) return config;
  return { ...config, run_scope: { split_ids, row_ids } };
}
