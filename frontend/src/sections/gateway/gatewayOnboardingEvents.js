import {
  appendSetupQuickStartAttributionToHref,
  setupQuickStartAttributionParams,
} from "src/sections/auth/jwt/setup-org-quick-starts";

const DEFAULT_GATEWAY_ID = "default";

export const GATEWAY_ONBOARDING_MODES = {
  ADD_POLICY: "add-policy",
  ADD_PROVIDER: "add-provider",
  CREATE_KEY: "create-key",
  FIX_FAILURE: "fix-failure",
  REVIEW_REQUEST: "review-request",
  TEST_REQUEST: "test-request",
};

const GATEWAY_JOURNEY_STEP_MODES = {
  add_gateway_policy: GATEWAY_ONBOARDING_MODES.ADD_POLICY,
  configure_gateway_provider: GATEWAY_ONBOARDING_MODES.ADD_PROVIDER,
  create_gateway_key: GATEWAY_ONBOARDING_MODES.CREATE_KEY,
  fix_gateway_failure: GATEWAY_ONBOARDING_MODES.FIX_FAILURE,
  review_gateway_log: GATEWAY_ONBOARDING_MODES.REVIEW_REQUEST,
  run_gateway_request: GATEWAY_ONBOARDING_MODES.TEST_REQUEST,
};
const VALID_GATEWAY_ONBOARDING_MODES = new Set(
  Object.values(GATEWAY_ONBOARDING_MODES),
);

const compactMetadata = (metadata) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  );

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

export const gatewaySetupQuickStartAttributionFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    quick_start_goal: params.get("quick_start_goal"),
    quick_start_id: params.get("quick_start_id"),
    quick_start_primary_path: params.get("quick_start_primary_path"),
  };
};

export const appendGatewayOnboardingAttributionToHref = (
  href,
  attributionOrSearch = {},
) =>
  appendSetupQuickStartAttributionToHref(
    href,
    attributionOrSearch instanceof URLSearchParams ||
      typeof attributionOrSearch === "string"
      ? gatewaySetupQuickStartAttributionFromSearch(attributionOrSearch)
      : attributionOrSearch,
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

export const getGatewayOnboardingRouteParams = (search = "") => {
  const params = toSearchParams(search);
  const rawMode = params.get("onboarding");
  const rawJourneyStep = params.get("journey_step");
  const journeyMode = GATEWAY_JOURNEY_STEP_MODES[rawJourneyStep];
  const mode = VALID_GATEWAY_ONBOARDING_MODES.has(rawMode)
    ? rawMode
    : journeyMode || null;

  return {
    isOnboarding: params.get("source") === "onboarding" || Boolean(mode),
    isFailureRepair:
      params.get("repair_request") === "1" ||
      rawJourneyStep === "fix_gateway_failure" ||
      mode === GATEWAY_ONBOARDING_MODES.FIX_FAILURE,
    mode,
    requestId: params.get("request_id"),
    tourAnchor: params.get("tour_anchor"),
  };
};

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

export const buildGatewayRequestReviewHref = ({
  quickStartAttribution,
  requestId,
  search,
} = {}) => {
  const params = new URLSearchParams();
  params.set("onboarding", "review-request");
  if (requestId) {
    params.set("request_id", requestId);
  }
  return appendGatewayOnboardingAttributionToHref(
    `/dashboard/gateway/logs?${params.toString()}`,
    quickStartAttribution ||
      gatewaySetupQuickStartAttributionFromSearch(search),
  );
};

export const buildGatewayPolicyConfigHref = ({
  isFailureRepair = false,
  policyType = "fallback",
  quickStartAttribution,
  requestId,
  search,
  tourAnchor,
} = {}) => {
  const policyPath =
    {
      budget: "/dashboard/gateway/budgets",
      fallback: "/dashboard/gateway/fallbacks",
      guardrail: "/dashboard/gateway/guardrails/configuration",
    }[policyType] || "/dashboard/gateway/fallbacks";
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("onboarding", GATEWAY_ONBOARDING_MODES.ADD_POLICY);
  params.set("journey_step", "add_gateway_policy");
  if (requestId) {
    params.set("request_id", requestId);
  }
  if (isFailureRepair) {
    params.set("repair_request", "1");
  }
  if (tourAnchor) {
    params.set("tour_anchor", tourAnchor);
  }

  return appendGatewayOnboardingAttributionToHref(
    `${policyPath}?${params.toString()}`,
    quickStartAttribution ||
      gatewaySetupQuickStartAttributionFromSearch(search),
  );
};

export const buildGatewayRequestSeenPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  quickStartAttribution,
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
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildGatewayProviderAddedPayload = ({
  apiFormat,
  gatewayId = DEFAULT_GATEWAY_ID,
  modelCount,
  providerName,
  quickStartAttribution,
  source = "gateway_provider_onboarding",
} = {}) => ({
  eventName: "gateway_provider_added",
  primaryPath: "gateway",
  stage: "configure_gateway_provider",
  source,
  artifactType: "gateway_provider",
  artifactId: safeKeyPart(providerName || gatewayId, "gateway-provider"),
  metadata: compactMetadata({
    gateway_id: gatewayId || DEFAULT_GATEWAY_ID,
    provider_name: providerName,
    api_format: apiFormat,
    model_count: modelCount,
  }),
  idempotencyKey: [
    "gateway_provider_added",
    safeKeyPart(providerName, "provider"),
    safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID),
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildGatewayKeyCreatedPayload = ({
  allowedModels = [],
  allowedProviders = [],
  gatewayId = DEFAULT_GATEWAY_ID,
  key,
  keyName,
  owner,
  quickStartAttribution,
  source = "gateway_key_onboarding",
} = {}) => {
  const keyId = key?.id || key?.gatewayKeyId || key?.gateway_key_id || keyName;

  return {
    eventName: "gateway_key_created",
    primaryPath: "gateway",
    stage: "create_gateway_key",
    source,
    artifactType: "gateway_key",
    artifactId: safeKeyPart(keyId || gatewayId, "gateway-key"),
    metadata: compactMetadata({
      gateway_id: gatewayId || DEFAULT_GATEWAY_ID,
      gateway_key_id: key?.gatewayKeyId || key?.gateway_key_id,
      key_id: key?.id,
      key_prefix: key?.keyPrefix || key?.key_prefix,
      key_name: keyName || key?.name,
      owner: owner || key?.owner,
      allowed_model_count: allowedModels.length,
      allowed_provider_count: allowedProviders.length,
    }),
    idempotencyKey: [
      "gateway_key_created",
      safeKeyPart(keyId, "key"),
      safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildGatewayPolicyCreatedPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  policyId,
  policyType,
  quickStartAttribution,
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
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildGatewayFailureResolvedPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  quickStartAttribution,
  repairType = "fallback",
  requestId,
  source,
  metadata = {},
} = {}) => ({
  eventName: "gateway_failure_resolved",
  primaryPath: "gateway",
  stage: "fix_gateway_failure",
  source: source || "gateway_failure_onboarding",
  artifactType: "request_log",
  artifactId: safeKeyPart(requestId || gatewayId, "gateway-request"),
  metadata: compactMetadata({
    gateway_id: gatewayId || DEFAULT_GATEWAY_ID,
    request_id: requestId,
    repair_type: repairType,
    ...metadata,
  }),
  idempotencyKey: [
    "gateway_failure_resolved",
    safeKeyPart(repairType, "repair"),
    safeKeyPart(requestId, "no-request"),
    safeKeyPart(gatewayId, DEFAULT_GATEWAY_ID),
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildGatewayFallbackPolicyCreatedPayload = ({
  gatewayId = DEFAULT_GATEWAY_ID,
  quickStartAttribution,
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
    quickStartAttribution,
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
  quickStartAttribution,
  quick_start_goal,
  quick_start_id,
  quick_start_primary_path,
  search,
} = {}) => {
  const params = new URLSearchParams();
  params.set("mode", "daily-quality");
  params.set("source", "onboarding");
  params.set("target_event", eventName);
  const payloadAttribution = {
    quick_start_goal,
    quick_start_id,
    quick_start_primary_path,
  };

  return appendGatewayOnboardingAttributionToHref(
    `/dashboard/home?${params.toString()}`,
    quickStartAttribution ||
      (payloadAttribution.quick_start_id
        ? payloadAttribution
        : gatewaySetupQuickStartAttributionFromSearch(search)),
  );
};
