import { readRetryAfterSeconds } from "./managedRefusalTiming";

const NO_RETRY = "The console will not retry automatically.";
const remedy = (category, title, body, label) => ({
  category,
  title,
  body: `${body} ${NO_RETRY}`,
  action: { label },
});

// The relay supplies bounded delta-seconds; direct response headers may carry
// an HTTP-date. A filled-window remedy still requires a strictly positive wait.
function positiveDelta(value) {
  const seconds = readRetryAfterSeconds(value);
  return seconds !== null && seconds > 0 ? seconds : null;
}

export function readSpendRefusal(detail) {
  if (
    detail.reason === "request_exceeds_minute_cap" ||
    detail.reason === "request_exceeds_hour_cap"
  ) {
    return remedy(
      "rate_limit",
      "Request exceeds the spending limit",
      "Lower max_tokens or reduce the request to fit within the spending limit.",
      "Lower max_tokens or reduce the request",
    );
  }
  if (detail.reason === "new_org_daily_cap") {
    return remedy(
      "rate_limit",
      "New organization spending limit reached",
      "New organizations have a daily spending limit for Future AGI credits. Contact support to raise the limit.",
      "Contact support",
    );
  }
  const seconds = positiveDelta(detail.retryAfter);
  // The filled-window producer has PaymentMeta and no reason. Unknown future
  // reasons must not borrow this guidance even if they also carry a retry hint.
  if (
    detail.remedy_hint === "retry" &&
    seconds !== null &&
    (detail.reason == null || detail.reason === "")
  ) {
    return remedy(
      "rate_limit",
      "Requests are temporarily limited",
      `Retry-After: ${seconds}. Wait before trying again. Use the same billing source. Setting up your own provider key is a separate manual choice.`,
      "Try again after waiting",
    );
  }
  return remedy(
    "rate_limit",
    "Managed spending is limited",
    "The response does not establish a reset window. Review the spending limit with an administrator before sending another request.",
    "Review the spending limit with an administrator",
  );
}

export function readBudgetStateRefusal(detail) {
  if (detail.reason === "redis_unavailable" && detail.remedy_hint === "retry") {
    return remedy(
      "billing_unavailable",
      "Budget state is unavailable",
      "Wait before trying again once on the same billing source. If it still fails, stop and ask an authorized administrator to check the durable budget state.",
      "Try again once after waiting",
    );
  }
  return remedy(
    "billing_unavailable",
    "Budget state is unavailable",
    "Ask an authorized administrator to correct missing activation, stale policy or unavailable durable budget state. Do not keep resending this request.",
    "Ask an administrator to check budget state",
  );
}

export function readAmbiguousModelRefusal(detail) {
  // Only exact provider-prefixed IDs from the documented candidates field are
  // displayed, as text. No URLs, arbitrary metadata or automatic selection.
  const candidates = Array.isArray(detail.candidates)
    ? [
        ...new Set(
          detail.candidates.filter(
            (id) =>
              typeof id === "string" &&
              id.length <= 256 &&
              /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._:@+-]*(?:\/[A-Za-z0-9][A-Za-z0-9._:@+-]*)*$/.test(
                id,
              ),
          ),
        ),
      ]
    : [];
  return {
    ...remedy(
      "other",
      "Managed model name is ambiguous",
      "Choose an exact provider-prefixed model ID from the candidates below. If no candidates are available, open the model catalog to choose an exact ID.",
      "Choose an exact provider-prefixed model ID",
    ),
    candidates,
  };
}
