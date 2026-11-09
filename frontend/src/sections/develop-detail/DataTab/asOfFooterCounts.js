/**
 * v2.1 g8 (versions:as-of): the total the Data footer compares the shown
 * rows against. Latest: the unfiltered page-0 read, never fewer than the grid
 * itself shows (an idle cache entry can lag a Save version). As of a version
 * (`?version=N`): that version's own size — its N1 row_count, else its own
 * unfiltered as-of read — so `?version=3` never says "N of <latest> rows".
 */
export function asOfFooterCounts({
  asOfVersion = null,
  versionRowCount,
  asOfTotal,
  latestTotal,
  shown,
  totalRows,
} = {}) {
  if (asOfVersion) {
    const own = versionRowCount ?? asOfTotal;
    return {
      total: own ?? shown ?? totalRows,
      source: own != null ? "version" : "grid",
    };
  }
  return {
    total: Math.max(latestTotal ?? 0, shown ?? 0) || totalRows,
    source: "latest",
  };
}
