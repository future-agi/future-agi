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
