// Display helpers for deployments: status words, times, digests.

export const BUSY_STATUSES = ["queued", "building", "deploying"];

export const isBusy = (status) => BUSY_STATUSES.includes(status);

const STATUS_LABELS = {
  queued: "Queued",
  building: "Building",
  deploying: "Deploying",
  live: "Live",
  failed: "Failed",
  superseded: "Superseded",
};

export const statusLabel = (status) => STATUS_LABELS[status] || "Unknown";

const HEALTH_LABELS = {
  ok: "Healthy",
  starting: "Starting",
  draining: "Draining",
  unavailable: "Unhealthy",
  unreachable: "Unreachable",
};

export const healthLabel = (status) => HEALTH_LABELS[status] || "Unknown";

const ACTION_LABELS = {
  deploy: "Deploy",
  rollback: "Rollback",
  token_rotation: "Token rotation",
};

export const actionLabel = (action) => ACTION_LABELS[action] || action;

/** "3 min ago", "in 2 h", "just now"; falls back to the date for old times. */
export function relativeTime(iso, now = Date.now()) {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "";
  const seconds = Math.round((now - at) / 1000);
  const abs = Math.abs(seconds);
  const unit = (value, word) =>
    seconds >= 0 ? `${value} ${word} ago` : `in ${value} ${word}`;
  if (abs < 45) return "just now";
  if (abs < 3600) return unit(Math.round(abs / 60), "min");
  if (abs < 86400) return unit(Math.round(abs / 3600), "h");
  if (abs < 7 * 86400) return unit(Math.round(abs / 86400), "d");
  return new Date(at).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function absoluteTime(iso) {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "";
  return new Date(at).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export const shortDigest = (hex) => (hex ? hex.slice(0, 12) : "");

/** "http://127.0.0.1:55408" -> "127.0.0.1:55408" */
export function displayUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname.replace(/\/$/, "")}`;
  } catch {
    return url || "";
  }
}

/** The revision record that is serving, if any. */
export const liveRevisionOf = (detail) =>
  detail?.liveRevision
    ? (detail.revisions || []).find(
        (r) => r.revision === detail.liveRevision,
      ) || null
    : null;
