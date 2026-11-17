/**
 * The status colour role of a request's HTTP status code in the guardrail
 * Logs: 246 is a warning; every 4xx and 5xx is an error (403 included, the
 * code a blocked request is recorded with); every 2xx is a success; any
 * other code, or none, is neutral.
 */
export function statusRole(code) {
  if (code === 246) return "warning";
  if (code >= 400 && code < 600) return "danger";
  if (code >= 200 && code < 300) return "success";
  return "neutral";
}
