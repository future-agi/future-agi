/**
 * Extract the raw value from an analytics field.
 *
 * The analytics overview API returns every metric as `{ value, trend }`.
 * Components that display these values must unwrap them first.
 *
 *   val({ value: 42, trend: 5 })  => 42
 *   val(42)                       => 42
 *   val(null)                     => null
 */
export function val(obj) {
  if (obj != null && typeof obj === "object" && "value" in obj)
    return obj.value;
  return obj;
}

/**
 * The analytics overview's cost with what costTotal needs to read it: the
 * total, how many of its requests had no price, and the request count.
 */
export function overviewCost(overview) {
  return {
    value: val(overview?.total_cost),
    unpriced: overview?.total_cost?.unpriced_requests,
    requests: val(overview?.total_requests),
  };
}

/**
 * Extract the trend percentage from an analytics field.
 *
 *   trend({ value: 42, trend: 5.2 })  => 5.2
 *   trend(42)                          => null
 */
export function trend(obj) {
  if (obj != null && typeof obj === "object" && "trend" in obj)
    return obj.trend;
  return null;
}

/**
 * A figure measured over requests (a latency, an error or cache-hit rate)
 * for a window or provider that served none: null, so it reads as missing
 * (EMPTY), never as "0 ms" or "0.0%". A count or a spend stays a true 0.
 * When the request count is unknown the figure is kept.
 *
 *   perRequest(0, 0)     => null
 *   perRequest(17, 0)    => 0
 *   perRequest(null, 42) => 42
 */
export function perRequest(requests, value) {
  const n = val(requests);
  if (n === null || n === undefined || n === "") return value;
  return Number(n) === 0 ? null : value;
}

/** Requests each period needs before a KPI's change is shown. */
export const MIN_TREND_SAMPLE = 20;

/**
 * Whether a period-over-period change means anything: this period and the
 * previous one each had at least MIN_TREND_SAMPLE requests. The overview
 * gives the previous count only through the requests trend
 * (previous = current / (1 + trend / 100)); with no trend, or at -100 %
 * (no requests now), it is not known, so no change is shown.
 *
 *   trendSampleOk({ value: 17, trend: -97.1 })  => false (17 now)
 *   trendSampleOk({ value: 300, trend: 2400 })  => false (12 before)
 *   trendSampleOk({ value: 500, trend: 25 })    => true  (400 before)
 */
export function trendSampleOk(totalRequests) {
  const current = Number(val(totalRequests));
  const change = trend(totalRequests);
  if (!Number.isFinite(current) || current < MIN_TREND_SAMPLE) return false;
  if (change == null || !Number.isFinite(Number(change))) return false;
  const factor = 1 + Number(change) / 100;
  if (factor <= 0) return false;
  return current / factor >= MIN_TREND_SAMPLE;
}
