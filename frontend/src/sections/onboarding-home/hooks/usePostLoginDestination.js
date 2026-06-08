import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getDeploymentPostLoginPath,
  useDeploymentMode,
} from "src/hooks/useDeploymentMode";
import {
  getFeatureFlagValue,
  isPostHogAvailable,
  onFeatureFlags,
} from "src/utils/PostHog";

import {
  fetchActivationState,
  onboardingHomeQueryKeys,
} from "../api/onboarding-home-api";
import {
  isSafePostLoginReturnTo,
  POST_LOGIN_REQUIRED_FLAGS,
  resolvePostLoginDestination,
  shouldPreserveCurrentDashboardRoute,
} from "../utils/post-login-routing";

const POST_LOGIN_QUERY_PARAMS = Object.freeze({
  source: "post_login",
  mode: "post_login",
});

const readFlag = (flagName, overrides = {}, { defaultValue = false } = {}) => {
  if (Object.prototype.hasOwnProperty.call(overrides, flagName)) {
    return overrides[flagName] === true;
  }
  if (!isPostHogAvailable()) {
    return defaultValue;
  }
  const rawValue = getFeatureFlagValue(flagName);
  if (rawValue === true) return true;
  if (rawValue === false) return false;
  return defaultValue;
};

const hasRequiredFlags = (flags = {}) =>
  POST_LOGIN_REQUIRED_FLAGS.every((flag) => flags?.[flag] === true);

const getBrowserPath = () => {
  if (typeof window === "undefined") return "";
  return `${window.location.pathname}${window.location.search}`;
};

export function usePostLoginDestination({
  currentPath,
  returnTo,
  user,
  flagOverrides,
  fallbackDestination: providedFallbackDestination,
} = {}) {
  const { mode } = useDeploymentMode();
  const [, setFlagVersion] = useState(0);
  const [featureFlagsReady, setFeatureFlagsReady] = useState(false);

  useEffect(() => {
    let active = true;
    const releaseFlagWait = () => {
      if (!active) return;
      setFeatureFlagsReady(true);
      setFlagVersion((version) => version + 1);
    };
    const timeout = setTimeout(releaseFlagWait, 500);
    const unsubscribe = onFeatureFlags(releaseFlagWait);
    return () => {
      active = false;
      clearTimeout(timeout);
      if (typeof unsubscribe === "function") unsubscribe();
    };
  }, []);

  const resolvedCurrentPath = currentPath ?? getBrowserPath();
  const fallbackDestination =
    providedFallbackDestination || getDeploymentPostLoginPath(mode);

  const flags = {
    onboarding_activation_state_api: readFlag(
      "onboarding_activation_state_api",
      flagOverrides,
      { defaultValue: true },
    ),
    onboarding_first_run_home: readFlag(
      "onboarding_first_run_home",
      flagOverrides,
      { defaultValue: true },
    ),
    onboarding_first_run_home_kill: readFlag(
      "onboarding_first_run_home_kill",
      flagOverrides,
    ),
    onboarding_release_0_internal: readFlag(
      "onboarding_release_0_internal",
      flagOverrides,
    ),
    onboarding_daily_quality_home: readFlag(
      "onboarding_daily_quality_home",
      flagOverrides,
    ),
  };

  const hasOrganizationRole = Boolean(user?.organization_role);
  const shouldCheckActivationForIncompleteUser =
    hasOrganizationRole && !user?.onboarding_completed;
  const shouldPreserveCurrentRoute = shouldPreserveCurrentDashboardRoute({
    currentPath: resolvedCurrentPath,
    fallbackDestination,
  });
  const safeReturnTo = isSafePostLoginReturnTo(returnTo);
  const canResolvePostLogin =
    hasOrganizationRole &&
    !user?.requires_org_setup &&
    (!safeReturnTo || shouldCheckActivationForIncompleteUser) &&
    (shouldCheckActivationForIncompleteUser || !shouldPreserveCurrentRoute);

  const requiredFlagsEnabled = hasRequiredFlags(flags);
  const postLoginHomeDisabled =
    flags?.onboarding_first_run_home === false ||
    flags?.onboarding_first_run_home_kill === true;
  const shouldFetchActivationState =
    canResolvePostLogin && requiredFlagsEnabled && !postLoginHomeDisabled;
  const isWaitingForFeatureFlags =
    canResolvePostLogin &&
    !requiredFlagsEnabled &&
    isPostHogAvailable() &&
    !featureFlagsReady;

  const activationStateQuery = useQuery({
    queryKey: onboardingHomeQueryKeys.activationState(POST_LOGIN_QUERY_PARAMS),
    queryFn: () => fetchActivationState(POST_LOGIN_QUERY_PARAMS),
    enabled: shouldFetchActivationState,
    retry: false,
    staleTime: 30 * 1000,
  });

  const activationState = shouldFetchActivationState
    ? activationStateQuery.data
    : null;
  const activationStateError = activationStateQuery.isError
    ? activationStateQuery.error
    : null;

  const destination = resolvePostLoginDestination({
    currentPath: resolvedCurrentPath,
    returnTo,
    user,
    deploymentMode: mode,
    fallbackDestination,
    flags,
    activationState,
    activationStateError,
  });

  const isResolving =
    isWaitingForFeatureFlags ||
    (shouldFetchActivationState &&
      !activationStateQuery.isError &&
      !activationStateQuery.data &&
      (activationStateQuery.isLoading || activationStateQuery.isFetching));

  return {
    activationState,
    activationStateError,
    destination,
    fallbackDestination,
    flags,
    isResolving,
    reason: destination.reason,
    deploymentMode: mode,
  };
}
