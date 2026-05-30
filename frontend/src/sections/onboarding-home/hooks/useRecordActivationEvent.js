import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  onboardingHomeQueryKeys,
  recordActivationEvent,
} from "../api/onboarding-home-api";
import {
  OnboardingHomeEvents,
  trackOnboardingHomeEvent,
} from "../analytics/onboarding-events";
import { normalizeSetupQuickStartAttribution } from "src/sections/auth/jwt/setup-org-quick-starts";

const compactProperties = (properties = {}) =>
  Object.entries(properties).reduce((result, [key, value]) => {
    if (value === undefined || value === null || value === "") return result;
    result[key] = value;
    return result;
  }, {});

const quickStartAttribution = (payload = {}) =>
  normalizeSetupQuickStartAttribution({
    quickStartGoal:
      payload.quickStartGoal ??
      payload.quick_start_goal ??
      payload.metadata?.quick_start_goal,
    quickStartId:
      payload.quickStartId ??
      payload.quick_start_id ??
      payload.metadata?.quick_start_id,
    quickStartPrimaryPath:
      payload.quickStartPrimaryPath ??
      payload.quick_start_primary_path ??
      payload.metadata?.quick_start_primary_path,
  });

const trackActivationEventRecorded = (activationState, payload = {}) => {
  const eventName = payload.eventName ?? payload.event_name;
  if (!eventName) return;
  const attribution = quickStartAttribution(payload);

  trackOnboardingHomeEvent(
    OnboardingHomeEvents.activationEventRecorded,
    compactProperties({
      activation_event_name: eventName,
      primary_path: payload.primaryPath ?? payload.primary_path,
      activation_stage: payload.stage,
      source: payload.source,
      campaign_key: payload.campaignKey ?? payload.campaign_key,
      email_key: payload.emailKey ?? payload.email_key,
      target_stage: payload.targetStage ?? payload.target_stage,
      target_event: payload.targetEvent ?? payload.target_event,
      send_log_id: payload.sendLogId ?? payload.send_log_id,
      email_status: payload.emailStatus ?? payload.email_status,
      link_issued_at: payload.linkIssuedAt ?? payload.link_issued_at,
      stale_reason: payload.staleReason ?? payload.stale_reason,
      context_status: payload.contextStatus ?? payload.context_status,
      quick_start_goal: attribution.quickStartGoal,
      quick_start_id: attribution.quickStartId,
      quick_start_primary_path: attribution.quickStartPrimaryPath,
      artifact_type: payload.artifactType ?? payload.artifact_type,
      artifact_id: payload.artifactId ?? payload.artifact_id,
      project_id: payload.projectId ?? payload.project_id,
      is_sample: payload.isSample ?? payload.is_sample,
      next_stage: activationState?.stage,
      next_primary_path: activationState?.primaryPath,
      next_is_activated: activationState?.isActivated,
      next_request_id: activationState?.requestId,
      workspace_id: activationState?.workspaceId,
      organization_id: activationState?.organizationId,
      user_id: activationState?.userId,
    }),
  );
};

export const useRecordActivationEvent = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: recordActivationEvent,
    onSuccess: (activationState, payload) => {
      trackActivationEventRecorded(activationState, payload);
      queryClient.invalidateQueries({
        queryKey: onboardingHomeQueryKeys.all,
      });
    },
  });
};
