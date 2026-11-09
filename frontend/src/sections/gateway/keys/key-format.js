// How an API key reads in the list and the drawer.

// Status chips on the Observe status chip (GatewayStatusChip tones).
export const KEY_STATUS = {
  active: { tone: "healthy", label: "Active" },
  revoked: { tone: "error", label: "Revoked locally" },
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

export function matchesKeySearch(key, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [key.name, key.owner, key.keyPrefix]
    .filter(Boolean)
    .some((text) => String(text).toLowerCase().includes(q));
}
