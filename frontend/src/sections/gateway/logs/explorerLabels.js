// Display labels for the request explorer's log entry v2 values. The value
// sets are the gateway's (plan 6.1, 6.2); a value this file does not know yet
// is shown as sent, so a newer gateway never renders a blank.

import { BILLING_MODE_LABELS, OWN_KEY_LABEL } from "../utils/providerLabel";

export const ATTEMPT_KINDS = {
  primary: { label: "Primary", color: "primary" },
  retry: { label: "Retry", color: "warning" },
  failover: { label: "Failover", color: "info" },
  model_fallback: { label: "Model fallback", color: "info" },
  auto_fallback: { label: "Auto fallback", color: "info" },
  request_fallback: { label: "Request fallback", color: "info" },
  hedge: { label: "Hedge", color: "secondary" },
  selector: { label: "Selector", color: "default" },
};

export const ATTEMPT_OUTCOMES = {
  success: { label: "Success", color: "success" },
  error: { label: "Error", color: "error" },
  timeout: { label: "Timed out", color: "error" },
  cancelled: { label: "Cancelled", color: "warning" },
  discarded: { label: "Discarded", color: "default" },
  skipped: { label: "Skipped", color: "default" },
  circuit_open: { label: "Circuit open", color: "warning" },
  hollow: { label: "Empty response", color: "warning" },
};

export const ERROR_CLASSES = {
  rate_limited: "Rate limited",
  timeout: "Timeout",
  upstream_5xx: "Upstream 5xx",
  upstream_4xx: "Upstream 4xx",
  context_length: "Context too long",
  content_filter: "Content filtered",
  auth: "Authentication",
  invalid_request: "Invalid request",
  network: "Network",
  circuit_open: "Circuit open",
  policy: "Policy",
  budget: "Budget",
  insufficient_credits: "Insufficient credits",
  cancelled: "Cancelled",
  unknown: "Unknown",
  hollow_completion: "Empty completion",
};

export const ATTEMPT_REASONS = {
  status_429: "Rate limited (429)",
  status_5xx: "Upstream error (5xx)",
  timeout: "Timeout",
  circuit_open: "Circuit open",
  context_length: "Context too long",
  content_filter: "Content filtered",
  network: "Network error",
  skipped_insufficient_credits: "Insufficient credits",
  retry_budget: "Retry budget spent",
  attempt_budget: "Attempt budget spent",
  deadline: "Deadline reached",
  policy: "Policy",
  tool_history_incompatible: "Tool history incompatible",
};

// A billing mode, and an attempt's credential type (the same two values),
// read the one pair every screen uses (utils/providerLabel.js).
export const CREDENTIAL_TYPES = { managed: "Future AGI", byok: OWN_KEY_LABEL };

export const BILLING_MODES = BILLING_MODE_LABELS;

// Where a request's price came from (the gateway's models.CostSource).
export const COST_SOURCES = {
  provider_reported: "Reported by the provider",
  catalog: "Catalog price",
  catalog_max: "Catalog price, highest of the model's providers",
  price_card: "Provider price card",
  modeldb: "Model database price",
  org_custom: "Your custom pricing",
  mixed: "Mixed: attempts priced differently",
  cache_hit: "Served from cache",
  unpriced: "No price found",
};

// The key the console's Try it makes for itself (agentcc/views/gateway.py
// _create_playground_api_key names it __playground_<org>_<hex>__).
const PLAYGROUND_KEY = /^__playground_[^]*__$/;

/** A key's name for people: the Try it key reads "Console Try it". */
export function apiKeyLabel(name) {
  if (typeof name !== "string" || name === "") return name;
  return PLAYGROUND_KEY.test(name) ? "Console Try it" : name;
}

export const STEPS = [
  "plan",
  "act",
  "tool",
  "extract",
  "verify",
  "reflect",
  "memory",
  "coordinate",
  "summarize",
  "final",
  "other",
];

export const POLICY_VARIANTS = { stable: "Stable", candidate: "Candidate" };

export const isKnownValue = (map, value) =>
  Object.prototype.hasOwnProperty.call(map, value);

const known = (map, value) => (isKnownValue(map, value) ? map[value] : null);

/** The label for a value in a label map, else the value itself. */
export function labelOf(map, value) {
  const entry = known(map, value);
  if (entry == null) return value;
  return typeof entry === "string" ? entry : entry.label;
}

/** The chip colour for a value in a label map, else "default". */
export function colorOf(map, value) {
  return known(map, value)?.color ?? "default";
}
