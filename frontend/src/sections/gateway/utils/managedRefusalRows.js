import { routingFieldGuidance } from "./managedRefusalFields";
import { readRetryAfterSeconds } from "./managedRefusalTiming";

// Additional status/code pairs from the r3 gateway contract. Text and actions
// are local; model names, policy IDs, param values and response links are not
// copied into the alert. An instruction never sends a request or changes policy.
const ROWS = [
  [
    402,
    "agent_call_cap_exceeded",
    "agent_budget",
    "Request exceeds the call budget",
    "Lower max_tokens",
  ],
  [
    402,
    "call_cap_unenforceable",
    "agent_budget",
    "Request budget cannot be enforced",
    "Choose a priced model or review the cap policy",
  ],
  [
    402,
    "agent_run_stopped",
    "agent_budget",
    "Agent run stopped",
    "Resolve the stop cause before starting a new run",
  ],
  [
    402,
    "managed_spend_paused",
    "budget_limit",
    "Managed spending is paused",
    "Ask an administrator to review the pause",
  ],
  [
    402,
    "payment_required",
    "out_of_credits",
    "Managed key credits are used up",
    "Top up the managed key balance",
  ],
  [
    429,
    "budget_throttled",
    "rate_limit",
    "Budget requests are throttled",
    "Try again after waiting",
  ],
  [
    503,
    "key_list_not_loaded",
    "other",
    "Gateway keys are loading",
    "Try again later",
  ],
  [
    403,
    "model_not_permitted",
    "permission",
    "Model is not permitted",
    "Choose an allowed model",
  ],
  [
    403,
    "managed_router_unbounded",
    "permission",
    "Managed routing needs a bounded model",
    "Choose a bounded priced model",
  ],
  [
    400,
    "unsupported_field_for_managed",
    "other",
    "Field is not supported for managed requests",
    "Remove the unsupported field",
  ],
  [
    400,
    "unsupported_routing_field",
    "other",
    "Routing field is not supported",
    "Remove the unsupported routing field",
  ],
  [
    403,
    "key_scope_denied",
    "permission",
    "Key scope does not allow this operation",
    "Ask an administrator for an appropriately scoped key",
  ],
  [
    402,
    "agent_key_budget_exhausted",
    "agent_budget",
    "Agent key budget exhausted",
    "Ask an administrator to review the key budget",
  ],
  [
    429,
    "token_rate_limit_exceeded",
    "rate_limit",
    "Token rate limit reached",
    "Try again after waiting",
  ],
  [
    429,
    "usage_paused",
    "budget_limit",
    "Usage is paused",
    "Ask an administrator to review the budget",
  ],
  [
    429,
    "quota_exceeded",
    "budget_limit",
    "Request quota exceeded",
    "Review the plan quota",
  ],
  [
    400,
    "usage_unavailable",
    "other",
    "Background usage cannot be billed",
    "Send without background mode",
  ],
  [
    503,
    "provider_busy",
    "rate_limit",
    "Provider is busy",
    "Try again after waiting",
  ],
  [
    409,
    "idempotency_in_progress",
    "other",
    "Request is still in progress",
    "Wait before resending the same request and idempotency key",
  ],
  [
    422,
    "idempotency_key_reused",
    "other",
    "Idempotency key was already used",
    "Use a new idempotency key for a different request",
  ],
  [
    404,
    "managed_models_disabled",
    "permission",
    "Managed models are unavailable",
    "Ask an operator to configure managed providers",
  ],
  [
    400,
    "deferred_run_unavailable",
    "other",
    "Deferred runs are unavailable",
    "Send the request synchronously",
  ],
  [
    404,
    "model_not_found",
    "other",
    "Model was not found",
    "Choose an available catalog model",
  ],
  [
    504,
    "gateway_timeout",
    "other",
    "Gateway request timed out",
    "Check request status before sending again",
  ],
  [
    410,
    "model_expired",
    "other",
    "Model is no longer active",
    "Choose an active replacement model",
  ],
  [
    404,
    "no_provider_matches",
    "other",
    "No eligible provider matches",
    "Adjust provider filters to include an eligible endpoint",
  ],
  [
    400,
    "provider_preference_unavailable",
    "other",
    "Provider preference is unavailable",
    "Remove or change the provider preference",
  ],
  [
    400,
    "model_ambiguous",
    "other",
    "Model name is ambiguous",
    "Use a complete catalog model ID",
  ],
  [
    400,
    "no_auto_candidate",
    "other",
    "No eligible automatic model",
    "Choose an eligible explicit model",
  ],
  [
    400,
    "omega_default_unroutable",
    "other",
    "Default model has no route",
    "Configure a routable default model",
  ],
  [
    400,
    "route_hint_unsupported",
    "other",
    "Route hint is not supported",
    "Use cost, balanced or quality for an automatic route",
  ],
  [
    400,
    "routing_field_not_yet_supported",
    "other",
    "Routing field is not available yet",
    "Remove the unimplemented routing field",
  ],
  [
    400,
    "invalid_provider_preference",
    "other",
    "Provider preference is invalid",
    "Correct or remove the provider preference",
  ],
  [
    400,
    "invalid_routing_field",
    "other",
    "Routing field is invalid",
    "Correct the routing field shape and limits",
  ],
  [
    400,
    "extra_body_conflict",
    "other",
    "Extra body conflicts with the request",
    "Remove the conflicting duplicate field",
  ],
  [
    400,
    "invalid_extra_body",
    "other",
    "Extra body is invalid",
    "Use a non-nested JSON object for extra_body",
  ],
  [
    404,
    "provider_not_found",
    "other",
    "Provider was not found",
    "Correct or remove the provider lock",
  ],
  // Pending in r3; accepting this code does not attest a deployed producer.
  [
    400,
    "managed_resource_unsupported",
    "other",
    "Managed request shape is not supported",
    "Use a supported managed request shape",
  ],
  // Forward r7 tuples; mapping support is not a producer/deployment claim.
  [
    400,
    "managed_async_unsupported",
    "other",
    "Managed asynchronous requests are not supported",
    "Send the request without x-agentcc-async",
  ],
  [
    400,
    "managed_output_ceiling_required",
    "other",
    "An output ceiling is required",
    "Set a positive max_tokens value",
  ],
  [
    400,
    "managed_unsupported_parameter",
    "other",
    "Managed request parameter is not supported",
    "Correct the request parameter",
  ],
  [
    400,
    "managed_openrouter_streaming_not_yet",
    "other",
    "OpenRouter streaming with credits is not available yet",
    "Explicitly choose your own provider key for OpenRouter streaming",
  ],
];
const MAPPINGS = new Map(
  ROWS.map(([status, code, category, title, label]) => [
    `${status}:${code}`,
    { category, title, label },
  ]),
);
const WAITABLE = new Set([
  "budget_throttled",
  "token_rate_limit_exceeded",
  "provider_busy",
  "idempotency_in_progress",
]);
const NOTES = {
  managed_output_ceiling_required:
    "Set a positive max_tokens value so this model has an output ceiling.",
  managed_unsupported_parameter:
    "Supply a valid request object; omit n/best_of or use 1; use positive max_tokens/max_completion_tokens; remove max_output_tokens, max_new_tokens, maxOutputTokens and max_tokens_to_sample from Extra. Do not retry the unchanged request.",
  managed_openrouter_streaming_not_yet:
    "For OpenRouter streaming, explicitly choose your own provider key. This does not automatically replay the request or switch billing source; this is not a transient credits-streaming failure.",
  call_cap_unenforceable:
    "Use a priced model with an enforceable bound, or ask an authorized administrator to correct the identity, route or cap policy; do not bypass the cap.",
  key_list_not_loaded:
    "An operator should check key synchronization if the problem continues.",
  model_not_permitted:
    "Only an authorized administrator can change the binding policy.",
  no_provider_matches:
    "Keep organization policy in place when changing request preferences.",
  provider_not_found:
    "Only an authorized operator can configure provider access.",
  invalid_routing_field:
    "Do not combine models and fallbacks; follow the documented list limits and shapes.",
  extra_body_conflict:
    "Keep model, messages, streaming, user, metadata and session fields at the top level.",
  idempotency_in_progress:
    "Keep the request body unchanged and resend with the same idempotency key.",
  gateway_timeout:
    "A timeout alone does not prove that the provider did not process the request.",
};

export function readAdditionalRefusal(httpStatus, errorCode, detail) {
  const row = MAPPINGS.get(`${httpStatus}:${errorCode}`);
  if (!row) return null;
  const retryAfter = readRetryAfterSeconds(detail.retryAfter);
  const guidance = WAITABLE.has(errorCode)
    ? `${retryAfter !== null ? `Retry-After: ${retryAfter}. ` : ""}Wait before trying again.`
    : `${row.label}.`;
  const note = Object.prototype.hasOwnProperty.call(NOTES, errorCode)
    ? ` ${NOTES[errorCode]}`
    : "";
  return {
    category: row.category,
    title: row.title,
    body: `${guidance}${note}${routingFieldGuidance(errorCode, detail.param)} The console will not retry automatically.`,
    action: { label: row.label },
  };
}
