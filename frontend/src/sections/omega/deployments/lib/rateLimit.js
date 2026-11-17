/**
 * Rate limits on the Deployments pages. A page is rate limited by the Omega
 * gateway (`omega_rate_limited`, a 429 with Retry-After) or by Future AGI's
 * own session exchange (`session_exchange_throttled`: 120 a minute per user).
 * The exchange's refusal reaches the page through the host's axios instance,
 * which keeps neither its status nor its Retry-After, so the code decides.
 */
const RATE_LIMIT_BASE_MS = 5_000;
const MAX_RATE_LIMIT_WAIT_MS = 60_000;

export function isRateLimited(error) {
  return (
    error?.status === 429 ||
    error?.code === "omega_rate_limited" ||
    error?.code === "session_exchange_throttled"
  );
}

/**
 * How long to wait before asking again after a rate-limited answer: never
 * sooner than the surface's normal interval, a positive Retry-After, or a
 * backoff that doubles with each rate-limited answer in a row (`count`), and
 * never longer than a minute, so the page comes back without a reload.
 */
export function rateLimitDelay(
  error,
  count = 1,
  normalInterval = RATE_LIMIT_BASE_MS,
) {
  const retryAfter = Number(error?.retryAfter);
  const serverDelay =
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1_000 : 0;
  const attempt = Math.max(1, Number(count) || 1);
  const backoff = RATE_LIMIT_BASE_MS * 2 ** Math.min(attempt - 1, 4);
  return Math.min(
    MAX_RATE_LIMIT_WAIT_MS,
    Math.max(normalInterval, serverDelay, backoff),
  );
}
