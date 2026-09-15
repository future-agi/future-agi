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

/** Release a cancelled datasource request without publishing stale/empty rows. */
export function settleCancelledGridRead(params, { retry = false } = {}) {
  return withLiveGridApi(params.api, (api) => {
    // AG Grid's shared concurrency slot is released only by a callback, even
    // when the request's original store has already been replaced.
    params.fail();
    // A refresh retaining the store must retry after the old block settles;
    // otherwise the old callback marks the newly refreshed block as failed.
    if (retry) api.retryServerSideLoads?.();
  });
}
