// How a call that ended before its answer finished reads, in one vocabulary
// for the Logs row, the request drawer and its timeline. Only structured
// fields the log API returns are read, never the error text:
// - the request's call_end, the gateway's record of why a call it sent ended
//   early (agentcc-gateway server/call_end.go): the client cancelled, the
//   provider stopped, the request's own deadline passed, or the gateway's
//   timeout for the model did. When it is there, it names the cause;
// - the request's usage_unreadable "dispatched_without_usage" (the gateway's
//   credits plugin, UsageDispatchedWithoutUsage): the gateway sent the latest
//   attempt to the provider and it ended without usage. The client left, a
//   timeout cut it, the connection was lost after the send, or a stream was
//   cut before its usage chunk. On Future AGI credits such a call is charged
//   once at the amount its attempt reserved, never voided, and its price
//   source reads catalog_max (credits chargeAtHold);
// - the latest sent attempt's outcome "cancelled": the gateway classifies an
//   attempt its caller cancelled that way.
// A log written before the gateway recorded call_end does not say which of
// the causes above ended a marked call, so it reads the neutral phrase unless
// its attempt says the client cancelled.

export const DISPATCHED_WITHOUT_USAGE = "dispatched_without_usage";

const LABELS = {
  client: "Cancelled by the client",
  provider: "Stopped by the provider",
  deadline: "Stopped at the request's deadline",
  gatewayTimeout: "Stopped at the gateway's model timeout",
  unrecorded: "Ended before the provider reported token counts",
};

const DESCRIPTIONS = {
  client: "The client closed the request before the answer finished.",
  provider: "The provider ended the call before the answer finished.",
  deadline: "The request's own time limit passed before the answer finished.",
  gatewayTimeout:
    "The gateway's time limit for this model passed before the answer finished.",
  unrecorded:
    "The request was sent to the provider and ended before the answer finished. The log does not say whether the client, the provider or the request's deadline ended it.",
};

// The gateway's call_end values.
const CALL_END_KINDS = {
  client_cancelled: "client",
  provider_stopped: "provider",
  deadline: "deadline",
  gateway_timeout: "gatewayTimeout",
};

/** Why a call that ended this way was charged at its hold. */
export const CHARGED_AT_HOLD =
  "The request was sent to the provider, which reported no token counts, so the reserved amount was charged.";

// Attempts the gateway never sent; its latest attempt is the last other one.
const NOT_SENT = new Set(["skipped", "circuit_open"]);

const text = (value) =>
  typeof value === "string" && value !== "" ? value : null;

/** The last attempt the gateway sent, in index order, or null. */
export function latestSentAttempt(attempts) {
  if (!Array.isArray(attempts)) return null;
  const sent = attempts
    .filter((attempt) => attempt && !NOT_SENT.has(attempt.outcome))
    .sort((a, b) => (Number(a.index) || 0) - (Number(b.index) || 0));
  return sent.length ? sent[sent.length - 1] : null;
}

/**
 * How a request's call ended when it ended before its answer finished:
 * { kind, label, description, attempt, chargedAtHold }, or null for any
 * other request. A list row carries no attempts, so the row reads call_end
 * and the mark only.
 */
export function callEndOf(log) {
  if (!log) return null;
  const meta = log.metadata || {};
  const billing = log.billing || {};
  const marked = meta.usage_unreadable === DISPATCHED_WITHOUT_USAGE;
  const attempt = latestSentAttempt(log.attempts);
  const cancelled = attempt?.outcome === "cancelled";
  const value = text(meta.call_end);
  const recorded =
    value && Object.prototype.hasOwnProperty.call(CALL_END_KINDS, value)
      ? CALL_END_KINDS[value]
      : null;
  if (!recorded && !marked && !cancelled) return null;

  const kind = recorded || (cancelled ? "client" : "unrecorded");
  const mode =
    text(meta.billing_mode) || text(billing.mode) || text(log.billing_mode);
  const source =
    text(meta.cost_source) ||
    text(billing.cost_source) ||
    text(log.cost_source);
  const billed = Number(log.cost);
  return {
    kind,
    label: LABELS[kind],
    description: DESCRIPTIONS[kind],
    attempt,
    chargedAtHold:
      marked &&
      mode === "managed" &&
      source === "catalog_max" &&
      Number.isFinite(billed) &&
      billed > 0,
  };
}
