// These are setup facts, not provider health, model eligibility or a promise
// that a paid request will succeed. Missing/read-error data stays unknown.
const READY = "ready";
const NOT_READY = "not-ready";
const UNKNOWN = "unknown";

function anyReady(states) {
  if (states.includes(READY)) return READY;
  return states.includes(UNKNOWN) ? UNKNOWN : NOT_READY;
}

function providerReadiness(providers) {
  // The providers read represents entries as a list or a name-keyed map.
  const entries = Array.isArray(providers)
    ? providers
    : providers && typeof providers === "object"
      ? Object.values(providers)
      : null;
  if (!entries) return UNKNOWN;
  return anyReady(entries.map((provider) => {
    if (provider?.pushed === true) return READY;
    if (provider?.pushed === false) return NOT_READY;
    return UNKNOWN;
  }));
}

function keyReadiness(key, now) {
  if (["revoked", "expired", "pending", "failed"].includes(key?.status)) {
    return NOT_READY;
  }
  if (key?.status !== "active") return UNKNOWN;
  const expiry = key.expiresAt ?? key.expires_at;
  if (expiry == null) return READY;
  const expiresAt = typeof expiry === "string" ? Date.parse(expiry) : NaN;
  const currentTime = now instanceof Date ? now.getTime() : now;
  if (!Number.isFinite(expiresAt) || !Number.isFinite(currentTime)) return UNKNOWN;
  return expiresAt > currentTime ? READY : NOT_READY;
}

function managedReadiness(status) {
  if (status?.available === false || status?.enabled === false) return NOT_READY;
  if (status?.available !== true || status?.enabled !== true) return UNKNOWN;
  const balance = status.balance_usd;
  if (
    (typeof balance !== "number" && typeof balance !== "string") ||
    (typeof balance === "string" && !balance.trim()) ||
    !Number.isFinite(Number(balance))
  ) return UNKNOWN;
  return Number(balance) > 0 ? READY : NOT_READY;
}

/**
 * BP-01 contract: pass the providers read's `providers` (array/name-keyed map),
 * useApiKeys' normalized array, and useManagedModelsStatus' `data`. Pass
 * undefined for a loading/failed read, not a synthetic empty list or retained
 * stale data. No queries are issued here.
 *
 * `source`, `key`, `managed` each return ready | not-ready | unknown.
 * A source is a pushed own-key provider OR available, enabled, funded credits.
 * `now` is an optional epoch-millisecond clock (or Date) for expiry checks.
 */
export function setupReadiness({ providers, keys, managedStatus, now = Date.now() } = {}) {
  const managed = managedReadiness(managedStatus);
  return {
    source: anyReady([providerReadiness(providers), managed]),
    key: Array.isArray(keys) ? anyReady(keys.map((key) => keyReadiness(key, now))) : UNKNOWN,
    managed,
  };
}
