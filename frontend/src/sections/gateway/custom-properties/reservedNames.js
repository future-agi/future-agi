// Custom properties reach the gateway as request metadata: the
// x-agentcc-metadata header (a JSON object) or the request body's `metadata`.
// The gateway drops caller keys it reserves for its own plugins, so a property
// declared under one of those names is never recorded on a request.
//
// Mirror of isBlockedMetadataKey in
// agentcc-gateway/internal/server/caller_metadata.go (prefixes, then exact
// names with their constants resolved). The console refuses these names
// before the request reaches the API.

const RESERVED_PREFIXES = [
  "auth_",
  "key_",
  "org_",
  "budget_",
  "ratelimit_",
  "cache_",
  "guardrail_",
  "credit_",
  "credits_",
  "rbac_",
  "auto_",
  "managed_",
  "agent_",
  "omega_",
  "run_",
  "step",
  "ledger_",
];

const RESERVED_NAMES = [
  "authorization",
  "client_ip",
  "timeout_ms",
  "cost",
  "cost_source",
  "cost_base",
  "cost_markup_percent",
  "billing_mode",
  "usage_estimated",
  "model_alias_requested",
  "routing_options",
  "upstream_provider_object",
  "billing_alert",
  "billing_modes_used",
  "stream_usage_requested",
  "source_choice",
  "outcome_class",
  "ocr_pages",
  "search_units",
];

export function reservedReason(name) {
  const lower = String(name || "")
    .trim()
    .toLowerCase();
  if (!lower) return null;
  const prefix = RESERVED_PREFIXES.find((p) => lower.startsWith(p));
  if (prefix) {
    return `Names starting with "${prefix}" are reserved by the gateway: the gateway drops it from every request, so it is never recorded.`;
  }
  if (RESERVED_NAMES.includes(lower)) {
    return `"${lower}" is a name the gateway sets itself: the gateway drops it from every request, so it is never recorded.`;
  }
  return null;
}

export function sampleValue(property) {
  const allowed = property?.allowed_values || [];
  if (allowed.length) return allowed[0];
  if (property?.property_type === "number") return 1;
  if (property?.property_type === "boolean") return true;
  return `<${property?.name || "value"}>`;
}

// How a request sends the organization's properties: the header, and the
// same object in the body's `metadata` (OpenAI-compatible).
export function sendSnippet(properties) {
  const recorded = (properties || []).filter((p) => !reservedReason(p.name));
  const example = Object.fromEntries(
    recorded.map((p) => [p.name, sampleValue(p)]),
  );
  const body = JSON.stringify(
    {
      model: "<model>",
      messages: [{ role: "user", content: "Hello" }],
      metadata: example,
    },
    null,
    2,
  );
  return {
    header: `x-agentcc-metadata: ${JSON.stringify(example)}`,
    body,
    count: recorded.length,
  };
}
