import { useQuery } from "@tanstack/react-query";
import { makeActivationStateErrorFallback } from "../activation-state-utils";
import {
  fetchActivationState,
  onboardingHomeQueryKeys,
} from "../api/onboarding-home-api";

const WAITING_STAGES = new Set([
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
]);

export const ACTIVATION_STATE_WAITING_POLL_MS = 6000;

const isWaitingForSignal = (state) =>
  Boolean(state && !state.isActivated && WAITING_STAGES.has(state.stage));

export const useActivationState = ({
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
  enabled = true,
  requireWorkspaceContext = true,
} = {}) => {
  const hasWorkspaceContext =
    !requireWorkspaceContext || Boolean(organizationId && workspaceId);
  const queryParams = {
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
  };
  const query = useQuery({
    queryKey: onboardingHomeQueryKeys.activationState(queryParams),
    queryFn: () => fetchActivationState(queryParams),
    enabled: enabled && hasWorkspaceContext,
    refetchInterval: ({ state: queryState }) =>
      isWaitingForSignal(queryState.data)
        ? ACTIVATION_STATE_WAITING_POLL_MS
        : false,
  });

  const state =
    query.data ||
    (query.isError ? makeActivationStateErrorFallback(query.error) : null);

  return {
    state,
    isLoading: query.isLoading,
    isRefetching: query.isRefetching,
    isError: query.isError,
    error: query.error,
    requestId: state?.requestId ?? null,
    refetch: query.refetch,
  };
};
