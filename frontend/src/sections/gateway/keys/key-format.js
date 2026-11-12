// How an API key reads in the list and the drawer.

import { relativeTime, toValidDate } from "src/utils/format-time";
import { localZoneName } from "../utils/timeZone";

// Status chips on the Observe status chip (GatewayStatusChip tones).
export const KEY_STATUS = {
  active: { tone: "healthy", label: "Active" },
  revoked: { tone: "error", label: "Revoked" },
  expired: { tone: "warning", label: "Expired" },
};

export const KEY_STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "revoked", label: "Revoked" },
  { value: "expired", label: "Expired" },
];

/**
 * The stored prefix, never the secret: the first and last four characters of
 * the key's random part ("sk-agentcc-4f2a…9c1d"), or, for a key made before
 * the backend stored both ends, how the gateway cut it ("sk-agentcc-4...").
 */
export function keyPrefixLabel(prefix) {
  if (!prefix) return "—";
  return prefix.includes("…") || prefix.endsWith("...")
    ? prefix
    : `${prefix}...`;
}

export function accessLabel(list, everything) {
  return list?.length ? list.join(", ") : everything;
}

// Keys that can still call the gateway come first (active, pending or failed),
// then expired keys, then revoked ones. Each group keeps the order the API
// sent, nothing is left out, and a column sort still reorders the list.
const STATUS_RANK = {
  active: 0,
  pending: 0,
  failed: 0,
  expired: 1,
  revoked: 2,
};

export function liveKeysFirst(keys) {
  const rank = (key) => STATUS_RANK[key.status] ?? 1;
  return [...keys].sort((a, b) => rank(a) - rank(b));
}

// The order statuses are counted in: the live ones first, as the list sorts.
const STATUS_ORDER = ["active", "pending", "failed", "expired", "revoked"];

// The line above the list: how many keys there are, how many in each status
// the keys have (any other status the API sends is counted too, after these,
// so the parts add up to the total) and the zone the times are in; "1 of 13
// keys" while a filter or search narrows the list.
export function keyCountSummary(keys, shown) {
  const total = keys.length;
  const noun = total === 1 ? "key" : "keys";
  const zone = `times in ${localZoneName()}`;
  if (shown !== total) return `${shown} of ${total} ${noun} · ${zone}`;
  const counts = new Map(STATUS_ORDER.map((status) => [status, 0]));
  keys.forEach((key) => {
    const status = key.status || "unknown";
    counts.set(status, (counts.get(status) || 0) + 1);
  });
  const byStatus = [...counts]
    .filter(([, count]) => count > 0)
    .map(([status, count]) => `${count} ${status}`);
  return [`${total} ${noun}`, ...byStatus, zone].join(" · ");
}

// The exact instant in the viewer's zone, named as the count line and Logs
// name it ("Oct 20, 2026, 08:00:00 America/Los_Angeles"), for a hover title
// or a drawer row; "" when there is none.
export function exactTime(value) {
  const date = toValidDate(value);
  if (!date) return "";
  const stamp = date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  return `${stamp} ${localZoneName()}`;
}

// When a key was last used, exactly and how long ago; "Never" when it has
// not been.
export function lastUsedDetail(value) {
  if (!toValidDate(value)) return "Never";
  return `${exactTime(value)} · ${relativeTime(value)}`;
}

export function matchesKeySearch(key, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [key.name, key.owner, key.keyPrefix]
    .filter(Boolean)
    .some((text) => String(text).toLowerCase().includes(q));
}
