// Exact field identifiers are local labels, not server prose or field values.
// Never derive labels by splitting/trimming a server string or accepting an
// arbitrary provider.*, model name, policy identifier or extra_body.* suffix.
const FIELD_LABELS = new Map(
  [
    "provider",
    "provider.order",
    "provider.only",
    "provider.ignore",
    "provider.allow_fallbacks",
    "provider.require_parameters",
    "provider.data_collection",
    "provider.zdr",
    "provider.quantizations",
    "provider.sort",
    "provider.sort.by",
    "provider.sort.partition",
    "provider.max_price",
    "provider.max_price.prompt",
    "provider.max_price.completion",
    "provider.max_price.request",
    "provider.max_price.image",
    "provider.preferred_max_latency",
    "provider.preferred_max_latency.p50",
    "provider.preferred_max_latency.p75",
    "provider.preferred_max_latency.p90",
    "provider.preferred_max_latency.p99",
    "provider.preferred_min_throughput",
    "provider.preferred_min_throughput.p50",
    "provider.preferred_min_throughput.p75",
    "provider.preferred_min_throughput.p90",
    "provider.preferred_min_throughput.p99",
    "providerOptions",
    "providerOptions.gateway",
    "providerOptions.gateway.order",
    "providerOptions.gateway.only",
    "providerOptions.gateway.sort",
    "providerOptions.gateway.models",
    "providerOptions.gateway.tags",
    "providerOptions.gateway.user",
    "providerOptions.gateway.sessionId",
    "providerOptions.gateway.zeroDataRetention",
    "models",
    "fallbacks",
    "route",
    "plugins",
    "transforms",
    "preset",
    "session_id",
    "caching",
    "extra_body",
    "extra_body.extra_body",
    "extra_body.model",
    "extra_body.messages",
    "extra_body.stream",
    "extra_body.stream_options",
    "extra_body.user",
    "extra_body.metadata",
    "extra_body.session_id",
    "extra_body.provider",
    "extra_body.providerOptions",
    "extra_body.models",
    "extra_body.fallbacks",
    "extra_body.route",
    "extra_body.plugins",
    "extra_body.transforms",
    "extra_body.preset",
    "extra_body.caching",
    "extra_body.max_tokens",
    "extra_body.temperature",
    "extra_body.top_p",
  ].map((field) => [field, field]),
);
const ROUTING_VALIDATION_CODES = new Set([
  "invalid_provider_preference",
  "invalid_routing_field",
  "unsupported_routing_field",
  "routing_field_not_yet_supported",
  "extra_body_conflict",
  "invalid_extra_body",
]);

const MANAGED_PARAMETER_LABELS = new Map(
  [
    "n",
    "best_of",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "max_new_tokens",
    "maxOutputTokens",
    "max_tokens_to_sample",
  ].map((field) => [field, field]),
);

export function routingFieldGuidance(errorCode, param) {
  const labels =
    errorCode === "managed_unsupported_parameter"
      ? MANAGED_PARAMETER_LABELS
      : ROUTING_VALIDATION_CODES.has(errorCode)
        ? FIELD_LABELS
        : null;
  const label = typeof param === "string" ? labels?.get(param) : null;
  return label ? ` Field: ${label}.` : "";
}
