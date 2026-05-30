import { useQuery } from "@tanstack/react-query";
import { makeActivationStateErrorFallback } from "../activation-state-utils";
import {
  fetchActivationState,
  onboardingHomeQueryKeys,
} from "../api/onboarding-home-api";

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
