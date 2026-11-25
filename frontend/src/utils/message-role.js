/** Keep provider role strings intact; malformed telemetry must still render. */
export function normalizeMessageRole(role, fallback = "unknown") {
  return typeof role === "string" && role ? role : fallback;
}
