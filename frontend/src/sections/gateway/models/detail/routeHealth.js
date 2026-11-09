// Live health for each route of a model, read from the org's own request
// logs (GET /agentcc/request-logs/), never from analytics/model-comparison:
// that groups by model only and names the busiest provider, so a model two
// providers serve would put one route's requests under the other.
//
// A route's requests are the logged requests with its provider and its model
// name. Per route the page asks for the count, the error count, and the
// requests at the 50th and 95th percentile of latency (one row each, by page,
// nearest rank). A route with no requests costs one request.

import { paths } from "src/routes/paths";

export const HEALTH_WINDOW_HOURS = 24;

/** The window's start: 24 hours back, on a whole minute (a stable query key). */
export function healthSince(now = Date.now()) {
  const start = now - HEALTH_WINDOW_HOURS * 3600 * 1000;
  return new Date(start - (start % 60000)).toISOString();
}

/**
 * The request-logs filter for one route, model a comma-separated "any of".
 * A managed route is the catalog id's managed requests ({ model,
 * billing_mode }), so its Open logs link never names the upstream reseller
 *. An own-key route ({ model, provider }) is that
 * provider under its own model
 * name, or the catalog id when a caller sent that; the catalog id is left out
 * when the managed route goes through the same provider, so no request is
 * counted under two routes. A model only the org's providers serve is read
 * under its page id and the name its provider lists.
 */
export function routeLogFilter(route, { modelId, routes = [] } = {}) {
  if (route.managed) return { model: route.sendAs, billing_mode: "managed" };
  const sharesManaged = routes.some(
    (r) => r.managed && r.provider === route.provider,
  );
  const models = [route.sendAs];
  if (modelId && modelId !== route.sendAs && !sharesManaged) {
    models.push(modelId);
  }
  // A caller may send the provider's own name for a model only the org's
  // providers serve (orgServed.js).
  if (route.listedAs && !models.includes(route.listedAs)) {
    models.push(route.listedAs);
  }
  return { model: models.join(","), provider: route.provider };
}

/** The 1-based row, sorted by latency, at percentile p (nearest rank). */
export function percentilePage(count, p) {
  if (!count) return null;
  return Math.min(count, Math.max(1, Math.ceil(p * count)));
}

const latencyAt = async (get, query, count, p) => {
  try {
    const data = await get({
      ...query,
      ordering: "latency_ms",
      page: percentilePage(count, p),
    });
    const value = data?.results?.[0]?.latency_ms;
    return typeof value === "number" ? value : null;
  } catch {
    // The count moved between requests (the page is gone): say nothing
    // rather than show another request's latency.
    return null;
  }
};

/**
 * One route's last 24 hours: { requests, errors, errorRate, p50, p95 }.
 * `get(params)` answers the request-logs list for those query params.
 */
export async function readRouteHealth(get, filter, since) {
  const query = { ...filter, started_after: since, limit: 1 };
  const requests = Number((await get(query))?.count) || 0;
  if (!requests) {
    return { requests: 0, errors: 0, errorRate: 0, p50: null, p95: null };
  }
  const [errorData, p50, p95] = await Promise.all([
    get({ ...query, is_error: true }),
    latencyAt(get, query, requests, 0.5),
    latencyAt(get, query, requests, 0.95),
  ]);
  const errors = Number(errorData?.count) || 0;
  return {
    requests,
    errors,
    errorRate: Number(((errors / requests) * 100).toFixed(2)),
    p50,
    p95,
  };
}

/** Logs, filtered: `filter` is { model, provider } as the route reads. */
export function logsLink(filter) {
  const query = new URLSearchParams(
    Object.entries(filter).filter(([, value]) => value),
  );
  return `${paths.dashboard.gateway.logs}?${query}`;
}

/** Every route's requests of the model: the models they send, any provider. */
export function modelLogFilter(routes, modelId) {
  const models = routes.flatMap((route) =>
    routeLogFilter(route, { modelId, routes }).model.split(","),
  );
  return { model: [...new Set(models.length ? models : [modelId])].join(",") };
}
