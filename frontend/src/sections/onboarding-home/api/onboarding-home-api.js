import axios, { endpoints } from "src/utils/axios";
import {
  normalizeActivationState,
  normalizeProductPath,
  normalizeSampleProject,
} from "../activation-state-utils";
import {
  normalizeSetupQuickStartAttribution,
  readPersistedSetupQuickStartAttribution,
} from "src/sections/auth/jwt/setup-org-quick-starts";

export const ONBOARDING_HOME_QUERY_KEY = "onboarding-home";

const compactObject = (value) =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null,
    ),
  );

export const onboardingHomeQueryKeys = {
  all: [ONBOARDING_HOME_QUERY_KEY],
  activationState: ({
    organizationId,
    workspaceId,
    source,
    campaignKey,
    emailKey,
    sendLogId,
    emailStatus,
    targetStage,
    targetEvent,
    targetRoute,
    linkIssuedAt,
    staleReason,
    contextStatus,
    mode,
    quickStartGoal,
    quickStartId,
    quickStartPrimaryPath,
  } = {}) => [
    ONBOARDING_HOME_QUERY_KEY,
    "activation-state",
    compactObject({
      organizationId,
      workspaceId,
      source,
      campaignKey,
      emailKey,
      sendLogId,
      emailStatus,
      targetStage,
      targetEvent,
      targetRoute,
      linkIssuedAt,
      staleReason,
      contextStatus,
      mode,
      quickStartGoal,
      quickStartId,
      quickStartPrimaryPath,
    }),
  ],
};

const unwrapPayload = (response) =>
  response?.data?.result ?? response?.data ?? response;

const normalizeActivationStatePayload = (payload) =>
  normalizeActivationState(payload?.activation_state ?? payload);

const normalizeSampleProjectPayload = (payload) => {
  const state = normalizeActivationStatePayload(payload);
  if (!payload?.sample_project) return state;
  return {
    ...state,
    sampleProject: normalizeSampleProject(payload.sample_project),
  };
};

const setupQuickStartAttributionParams = (params = {}) => {
  const attribution = normalizeSetupQuickStartAttribution({
    quickStartGoal: params.quickStartGoal ?? params.quick_start_goal,
    quickStartId: params.quickStartId ?? params.quick_start_id,
    quickStartPrimaryPath:
      params.quickStartPrimaryPath ?? params.quick_start_primary_path,
  });
  return compactObject({
    quick_start_goal: attribution.quickStartGoal,
    quick_start_id: attribution.quickStartId,
    quick_start_primary_path: attribution.quickStartPrimaryPath,
  });
};

const activationStateParams = (params = {}) =>
  compactObject({
    source: params.source,
    campaign_key: params.campaignKey ?? params.campaign_key,
    email_key: params.emailKey ?? params.email_key,
    send_log_id: params.sendLogId ?? params.send_log_id,
    email_status: params.emailStatus ?? params.email_status,
    target_stage: params.targetStage ?? params.target_stage,
    target_event: params.targetEvent ?? params.target_event,
    target_route: params.targetRoute ?? params.target_route,
    link_issued_at: params.linkIssuedAt ?? params.link_issued_at,
    stale_reason: params.staleReason ?? params.stale_reason,
    context_status: params.contextStatus ?? params.context_status,
    mode: params.mode,
    ...setupQuickStartAttributionParams(params),
  });

const goalPayload = (payload = {}) =>
  compactObject({
    goal: payload.goal,
    primary_path:
      normalizeProductPath(payload.primaryPath ?? payload.primary_path) ||
      undefined,
    persona: payload.persona,
    source: payload.source,
    campaign_key: payload.campaignKey ?? payload.campaign_key,
    reason: payload.reason,
    expected_stage: payload.expectedStage ?? payload.expected_stage,
    known_goal_id: payload.knownGoalId ?? payload.known_goal_id,
  });

const activationEventMetadata = (metadata) => {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return metadata;
  }

  return Object.fromEntries(
    Object.entries(metadata)
      .filter(([, value]) => value !== undefined)
      .map(([key, value]) => {
        if (value === null || typeof value === "string") {
          return [key, value];
        }
        if (typeof value === "object") {
          return [key, JSON.stringify(value)];
        }
        return [key, String(value)];
      }),
  );
};

const activationAttributionMetadata = (payload = {}) => {
  const topLevelAttribution = normalizeSetupQuickStartAttribution({
    quickStartGoal: payload.quickStartGoal ?? payload.quick_start_goal,
    quickStartId: payload.quickStartId ?? payload.quick_start_id,
    quickStartPrimaryPath:
      payload.quickStartPrimaryPath ?? payload.quick_start_primary_path,
  });
  const metadataAttribution = normalizeSetupQuickStartAttribution({
    quickStartGoal:
      payload.metadata?.quickStartGoal ?? payload.metadata?.quick_start_goal,
    quickStartId:
      payload.metadata?.quickStartId ?? payload.metadata?.quick_start_id,
    quickStartPrimaryPath:
      payload.metadata?.quickStartPrimaryPath ??
      payload.metadata?.quick_start_primary_path,
  });
  const attribution = topLevelAttribution.quickStartId
    ? topLevelAttribution
    : metadataAttribution.quickStartId
      ? metadataAttribution
      : readPersistedSetupQuickStartAttribution();
  return compactObject({
    quick_start_goal: attribution.quickStartGoal,
    quick_start_id: attribution.quickStartId,
    quick_start_primary_path: attribution.quickStartPrimaryPath,
  });
};

const QUICK_START_METADATA_KEYS = new Set([
  "quick_start_goal",
  "quick_start_id",
  "quick_start_primary_path",
]);

const removeQuickStartMetadata = (metadata = {}) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([key]) => !QUICK_START_METADATA_KEYS.has(key),
    ),
  );

const activationEventPayloadMetadata = (payload = {}) => {
  const metadata = {
    ...removeQuickStartMetadata(payload.metadata || {}),
    ...activationAttributionMetadata(payload),
  };
  return Object.keys(metadata).length
    ? activationEventMetadata(metadata)
    : undefined;
};

export const fetchActivationState = async (params = {}) => {
  const response = await axios.get(endpoints.onboarding.activationState, {
    params: activationStateParams(params),
  });
  return normalizeActivationStatePayload(unwrapPayload(response));
};

export const saveOnboardingGoal = async (payload = {}) => {
  const response = await axios.post(
    endpoints.onboarding.goal,
    goalPayload(payload),
  );
  return normalizeActivationStatePayload(unwrapPayload(response));
};

const activationEventPayload = (payload = {}) =>
  compactObject({
    event_name: payload.eventName ?? payload.event_name,
    primary_path:
      normalizeProductPath(payload.primaryPath ?? payload.primary_path) ||
      undefined,
    stage: payload.stage,
    source: payload.source,
    artifact_type: payload.artifactType ?? payload.artifact_type,
    artifact_id: payload.artifactId ?? payload.artifact_id,
    project_id: payload.projectId ?? payload.project_id,
    metadata: activationEventPayloadMetadata(payload),
    idempotency_key: payload.idempotencyKey ?? payload.idempotency_key,
    is_sample: payload.isSample ?? payload.is_sample,
    campaign_key: payload.campaignKey ?? payload.campaign_key,
    email_key: payload.emailKey ?? payload.email_key,
    send_log_id: payload.sendLogId ?? payload.send_log_id,
    email_status: payload.emailStatus ?? payload.email_status,
    target_stage: payload.targetStage ?? payload.target_stage,
    target_event: payload.targetEvent ?? payload.target_event,
    link_issued_at: payload.linkIssuedAt ?? payload.link_issued_at,
    stale_reason: payload.staleReason ?? payload.stale_reason,
    context_status: payload.contextStatus ?? payload.context_status,
  });

export const recordActivationEvent = async (payload = {}) => {
  const response = await axios.post(
    endpoints.onboarding.activationEvent,
    activationEventPayload(payload),
  );
  return normalizeActivationStatePayload(unwrapPayload(response));
};

export class OnboardingEndpointUnavailableError extends Error {
  constructor(endpointName) {
    super(`${endpointName} endpoint is not available yet`);
    this.name = "OnboardingEndpointUnavailableError";
    this.endpointName = endpointName;
  }
}

const sampleEndpoint = (key) => endpoints.onboarding?.[key];

const sampleProjectPayload = (payload = {}) =>
  compactObject({
    path:
      normalizeProductPath(payload.path ?? payload.primaryPath) || "observe",
    manifest_id: payload.manifestId ?? payload.manifest_id,
    manifest_version: payload.manifestVersion ?? payload.manifest_version,
    source: payload.source,
    reason: payload.reason,
    open_after_create: payload.openAfterCreate ?? payload.open_after_create,
    campaign_key: payload.campaignKey ?? payload.campaign_key,
    email_key: payload.emailKey ?? payload.email_key,
    send_log_id: payload.sendLogId ?? payload.send_log_id,
    email_status: payload.emailStatus ?? payload.email_status,
    target_stage: payload.targetStage ?? payload.target_stage,
    target_event: payload.targetEvent ?? payload.target_event,
    link_issued_at: payload.linkIssuedAt ?? payload.link_issued_at,
    stale_reason: payload.staleReason ?? payload.stale_reason,
    context_status: payload.contextStatus ?? payload.context_status,
    ...setupQuickStartAttributionParams(payload),
  });

export const openSampleProject = async (payload = {}) => {
  const endpoint = sampleEndpoint("sampleProject");
  if (!endpoint) {
    throw new OnboardingEndpointUnavailableError("sampleProject");
  }
  const response = await axios.post(endpoint, sampleProjectPayload(payload));
  return normalizeSampleProjectPayload(unwrapPayload(response));
};

const sendTestTracePayload = (payload = {}) =>
  compactObject({
    path:
      normalizeProductPath(payload.path ?? payload.primaryPath) || "observe",
    project_id: payload.projectId ?? payload.project_id,
    source: payload.source,
    reason: payload.reason,
    campaign_key: payload.campaignKey ?? payload.campaign_key,
    email_key: payload.emailKey ?? payload.email_key,
    send_log_id: payload.sendLogId ?? payload.send_log_id,
    email_status: payload.emailStatus ?? payload.email_status,
    target_stage: payload.targetStage ?? payload.target_stage,
    target_event: payload.targetEvent ?? payload.target_event,
    link_issued_at: payload.linkIssuedAt ?? payload.link_issued_at,
    stale_reason: payload.staleReason ?? payload.stale_reason,
    context_status: payload.contextStatus ?? payload.context_status,
    ...setupQuickStartAttributionParams(payload),
  });

// Sends a clearly-labelled TEST trace so a first-run user gets a deterministic
// first signal without real traffic. The resulting trace advances the waiting
// state to trace-ready/review but does NOT count as real activation
// (first_quality_loop_completed stays unset). Gated by
// signals.test_trace_supported so the button never renders against a backend
// that lacks the endpoint.
export const sendTestTrace = async (payload = {}) => {
  const endpoint = sampleEndpoint("sendTestTrace");
  if (!endpoint) {
    throw new OnboardingEndpointUnavailableError("sendTestTrace");
  }
  const response = await axios.post(endpoint, sendTestTracePayload(payload));
  return normalizeActivationStatePayload(unwrapPayload(response));
};

export const hideSampleProject = async (payload = {}) => {
  const endpoint = sampleEndpoint("hideSampleProject");
  if (!endpoint) {
    throw new OnboardingEndpointUnavailableError("hideSampleProject");
  }
  const response = await axios.post(
    endpoint,
    compactObject({
      source: payload.source,
      reason: payload.reason,
    }),
  );
  return normalizeActivationStatePayload(unwrapPayload(response));
};
