import axios, { endpoints } from "src/utils/axios";
import {
  normalizeActivationState,
  normalizeProductPath,
  normalizeSampleProject,
} from "../activation-state-utils";

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
    targetStage,
    targetEvent,
    targetRoute,
    linkIssuedAt,
    mode,
  } = {}) => [
    ONBOARDING_HOME_QUERY_KEY,
    "activation-state",
    compactObject({
      organizationId,
      workspaceId,
      source,
      campaignKey,
      emailKey,
      targetStage,
      targetEvent,
      targetRoute,
      linkIssuedAt,
      mode,
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

const activationStateParams = (params = {}) =>
  compactObject({
    source: params.source,
    campaign_key: params.campaignKey ?? params.campaign_key,
    email_key: params.emailKey ?? params.email_key,
    target_stage: params.targetStage ?? params.target_stage,
    target_event: params.targetEvent ?? params.target_event,
    target_route: params.targetRoute ?? params.target_route,
    link_issued_at: params.linkIssuedAt ?? params.link_issued_at,
    mode: params.mode,
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
    metadata: activationEventMetadata(payload.metadata),
    idempotency_key: payload.idempotencyKey ?? payload.idempotency_key,
    is_sample: payload.isSample ?? payload.is_sample,
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
  });

export const openSampleProject = async (payload = {}) => {
  const endpoint = sampleEndpoint("sampleProject");
  if (!endpoint) {
    throw new OnboardingEndpointUnavailableError("sampleProject");
  }
  const response = await axios.post(endpoint, sampleProjectPayload(payload));
  return normalizeSampleProjectPayload(unwrapPayload(response));
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
