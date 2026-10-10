/**
 * AG Grid keeps datasource callbacks alive after a React grid is replaced.
 * Calling an API from one of those callbacks can restart server-side loads or
 * retain destroyed grid state, so every asynchronous boundary must fail
 * closed once the owning grid has gone away.
 */
export function isGridApiLive(api) {
  if (!api) return false;

  try {
    return typeof api.isDestroyed !== "function" || api.isDestroyed() !== true;
  } catch {
    return false;
  }
}

export function withLiveGridApi(api, callback) {
  if (!isGridApiLive(api)) return false;
  callback(api);
  return true;
}

/**
 * Re-read a server-side grid's rows from its datasource.
 *
 * Observe list grids (TraceGrid, SpanGrid) keep each visited page for cursor
 * pagination, and a bare refreshServerSide() is answered from that memory
 * without a request. Those grids put `reloadList(purge)` on their grid context;
 * it clears the memory first. Other grids get refreshServerSide().
 */
export function reloadServerSideGrid(api, { purge = false } = {}) {
  return withLiveGridApi(api, (liveApi) => {
    const reloadList = liveApi.getGridOption?.("context")?.reloadList;
    if (typeof reloadList === "function") reloadList(purge);
    else liveApi.refreshServerSide?.({ purge });
  });
}
