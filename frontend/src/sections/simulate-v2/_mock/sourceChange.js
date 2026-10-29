/*
  A scenario grounded in an attached file whose newer version is waiting
  (refreshed with "Keep pinned"): it says Source changed until it's
  re-grounded, or kept on the version it was made from.
*/

/** The waiting change for a scenario's source, or null. */
export const sourceChangeOf = (row, envState) => {
  if (!row?.citedSource) return null;
  const source = (envState?.sources || []).find(
    (s) => s.id === row.citedSource,
  );
  const pending = source?.pendingRefresh;
  if (!pending) return null;
  if (
    row.sourcePinned?.sourceId === source.id &&
    row.sourcePinned.v >= source.version &&
    row.sourcePinned.against === pending.v
  )
    return null;
  return { source, pending };
};

/** Keep this case on the source version it was made from; noted on the row. */
export const keepCaseOnSourcePatch = (envState, rowId, source) => ({
  scenarios: (envState?.scenarios || []).map((r) =>
    r.id === rowId
      ? {
          ...r,
          sourcePinned: {
            sourceId: source.id,
            v: source.version,
            against: source.pendingRefresh?.v,
            at: new Date().toISOString(),
          },
        }
      : r,
  ),
});
