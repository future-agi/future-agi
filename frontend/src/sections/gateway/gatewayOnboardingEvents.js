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

const headerValue = (headers = {}, names = []) => {
  const normalized = Object.fromEntries(
    Object.entries(headers || {}).map(([key, value]) => [
      String(key).toLowerCase(),
      value,
    ]),
  );
  const name = names.find((candidate) =>
    Object.prototype.hasOwnProperty.call(normalized, candidate),
  );
  return name ? normalized[name] : null;
};

export const gatewayPlaygroundResult = (payload = {}) =>
  payload?.result || payload || {};

export const gatewayPlaygroundRequestId = (payload = {}) => {
  const result = gatewayPlaygroundResult(payload);
  const headers = result.guardrail_headers || result.guardrailHeaders || {};
  return (
    result.request_id ||
    result.requestId ||
    headerValue(headers, [
      "x-agentcc-request-id",
      "x-request-id",
      "x-correlation-id",
    ]) ||
    null
  );
};

export const buildGatewayRequestReviewHref = ({ requestId } = {}) => {
  const params = new URLSearchParams();
  params.set("onboarding", "review-request");
  if (requestId) {
    params.set("request_id", requestId);
  }
  return `/dashboard/gateway/logs?${params.toString()}`;
};

export const buildGatewayRequestSeenPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  result,
  source = "gateway_request_onboarding",
} = {}) => {
  const normalizedResult = gatewayPlaygroundResult(result);
  const requestId = gatewayPlaygroundRequestId(normalizedResult);
  const statusCode =
    normalizedResult.status_code ?? normalizedResult.statusCode ?? null;
  const responseId =
    normalizedResult.body?.id || normalizedResult.response_id || null;
  const artifactId =
    requestId ||
    responseId ||
    `gateway-request-${safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID)}`;

  return {
    eventName: "gateway_request_seen",
    primaryPath: "gateway",
    stage: "run_gateway_request",
    source,
    artifactType: "gateway_request",
    artifactId: safeKeyPart(artifactId, "gateway-request"),
    metadata: compactMetadata({
      gateway_id: gatewayId || DEFAULT_GATEWAY_ID,
      request_id: requestId,
      response_id: responseId,
      status_code: statusCode,
      is_error: Boolean(
        normalizedResult.blocked ||
          normalizedResult.error ||
          (statusCode && Number(statusCode) >= 400),
      ),
      model: normalizedResult.model,
      blocked: normalizedResult.blocked,
      warned: normalizedResult.warned,
    }),
    idempotencyKey: [
      "gateway_request_seen",
      safeKeyPart(requestId || responseId, "no-request"),
      safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID),
    ].join(":"),
    isSample: false,
  };
};

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

export const buildGatewayOnboardingCompletionHref = ({
  eventName = "gateway_policy_created",
} = {}) => {
  const params = new URLSearchParams();
  params.set("mode", "daily-quality");
  params.set("source", "onboarding");
  params.set("target_event", eventName);

  return `/dashboard/home?${params.toString()}`;
};
