const DEFAULT_GATEWAY_ID = "default";

const compactMetadata = (metadata) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  );

const safeKeyPart = (value, fallback) =>
  String(value || fallback)
    .replace(/\s+/g, "-")
    .slice(0, 56);

const fallbackChainsForRouting = (routing = {}) =>
  routing.model_fallbacks || routing.modelFallbacks || {};

export const buildGatewayPolicyCreatedPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  policyId,
  policyType,
  requestId,
  source,
  metadata = {},
} = {}) => {
  const normalizedPolicyType = policyType || "gateway";
  const normalizedPolicyId = policyId || normalizedPolicyType;

  return {
    eventName: "gateway_policy_created",
    primaryPath: "gateway",
    stage: "add_gateway_policy",
    source: source || "gateway_policy_onboarding",
    artifactType: "gateway_policy",
    artifactId: safeKeyPart(
      requestId || normalizedPolicyId || gatewayId,
      "gateway-policy",
    ),
    metadata: compactMetadata({
      gateway_id: gatewayId || DEFAULT_GATEWAY_ID,
      request_id: requestId,
      policy_type: normalizedPolicyType,
      policy_id: normalizedPolicyId,
      gateway_synced: true,
      ...metadata,
    }),
    idempotencyKey: [
      "gateway_policy_created",
      safeKeyPart(normalizedPolicyType, "gateway"),
      safeKeyPart(requestId, "no-request"),
      safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID),
    ].join(":"),
    isSample: false,
  };
};

export const buildGatewayFallbackPolicyCreatedPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  requestId,
  routing,
  source = "gateway_fallbacks_onboarding",
} = {}) => {
  const fallbackChains = fallbackChainsForRouting(routing);
  const chainCount = Object.keys(fallbackChains).filter(Boolean).length;

  return buildGatewayPolicyCreatedPayload({
    gatewayId,
    policyId: requestId ? `fallback:${requestId}` : "fallback",
    policyType: "fallback",
    requestId,
    source,
    metadata: {
      fallback_chain_count: chainCount,
      fallback_enabled: Boolean(
        routing?.fallback_enabled ?? routing?.fallbackEnabled ?? true,
      ),
    },
  });
};
