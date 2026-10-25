// The request explorer's agent and billing filters: their camelCase keys in
// the filters object, and the snake_case name each takes in the URL and in
// the request logs' list, search and export query params. Every one takes a
// comma-separated list and matches any of its values.

export const AGENT_BILLING_FILTER_PARAMS = {
  billingMode: "billing_mode",
  runId: "run_id",
  agentId: "agent_id",
  step: "step",
  policyVariant: "policy_variant",
  errorClass: "error_class",
};

const LIST_KEYS = new Set(["step", "errorClass"]);

// The filter panel's values: a list for multi-select fields, else a string.
export const EMPTY_AGENT_BILLING_FILTERS = Object.freeze({
  billingMode: "",
  runId: "",
  agentId: "",
  step: [],
  policyVariant: "",
  errorClass: [],
});

const splitList = (value) =>
  value
    ? String(value)
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
    : [];

/** The panel's values for the filters currently applied. */
export function readAgentBillingFilters(filters) {
  const local = {};
  for (const key of Object.keys(AGENT_BILLING_FILTER_PARAMS)) {
    local[key] = LIST_KEYS.has(key)
      ? splitList(filters[key])
      : filters[key] || "";
  }
  return local;
}

/** The applied filters for the panel's values; empty values are left out. */
export function writeAgentBillingFilters(local) {
  const out = {};
  for (const key of Object.keys(AGENT_BILLING_FILTER_PARAMS)) {
    const value = LIST_KEYS.has(key)
      ? (local[key] || []).join(",")
      : String(local[key] || "").trim();
    if (value) out[key] = value;
  }
  return out;
}

/** The query params these filters add to an API call. */
export function agentBillingParams(filters) {
  const params = {};
  for (const [key, param] of Object.entries(AGENT_BILLING_FILTER_PARAMS)) {
    if (filters[key]) params[param] = filters[key];
  }
  return params;
}

// ---------------------------------------------------------------------------
// Date filters. The panel's <input type="datetime-local"> holds a
// local wall time with no zone ("2026-10-19T10:30"). The applied filter and
// the API take ISO 8601 with an offset (Date.toISOString(): "...Z"); the API
// reads a value with no offset as UTC, so the console never sends one.
// ---------------------------------------------------------------------------

const HAS_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i;
const pad = (n) => String(n).padStart(2, "0");

/** A datetime-local value (local wall time) as ISO 8601 UTC; "" if unreadable. */
export function localInputToIso(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

/**
 * An applied filter value as the datetime-local input shows it: a value with
 * an offset in this browser's local time; a value with none as the local time
 * it was typed in.
 */
export function isoToLocalInput(value) {
  if (!value) return "";
  if (!HAS_OFFSET.test(value)) return String(value).slice(0, 16);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** A date filter as the API gets it: always with an offset. */
export function dateFilterParam(value) {
  if (!value) return value;
  return HAS_OFFSET.test(value) ? value : localInputToIso(value);
}

/** This browser's time zone and its UTC offset at `at`, e.g. "Asia/Kolkata (UTC+05:30)". */
export function timeZoneLabel(at = new Date()) {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
  const minutes = -at.getTimezoneOffset();
  const sign = minutes >= 0 ? "+" : "-";
  const abs = Math.abs(minutes);
  return `${zone} (UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)})`;
}
