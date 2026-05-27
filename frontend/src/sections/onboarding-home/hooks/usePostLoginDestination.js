import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  getDeploymentPostLoginPath,
  useDeploymentMode,
} from "src/hooks/useDeploymentMode";
import { isFeatureEnabled, onFeatureFlags } from "src/utils/PostHog";

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

const readFlag = (flagName, overrides = {}) => {
  if (Object.prototype.hasOwnProperty.call(overrides, flagName)) {
    return overrides[flagName] === true;
  }
  return isFeatureEnabled(flagName) === true;
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
  const [flagVersion, setFlagVersion] = useState(0);
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

  const flags = useMemo(
    () => ({
      onboarding_activation_state_api: readFlag(
        "onboarding_activation_state_api",
        flagOverrides,
      ),
      onboarding_first_run_home: readFlag(
        "onboarding_first_run_home",
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
    }),
    [flagOverrides, flagVersion],
  );

  const canResolvePostLogin =
    Boolean(user?.organization_role) &&
    Boolean(user?.onboarding_completed) &&
    !user?.requires_org_setup &&
    !isSafePostLoginReturnTo(returnTo) &&
    !shouldPreserveCurrentDashboardRoute({
      currentPath: resolvedCurrentPath,
      fallbackDestination,
    });

  const requiredFlagsEnabled = hasRequiredFlags(flags);
  const shouldFetchActivationState =
    canResolvePostLogin && requiredFlagsEnabled;
  const isWaitingForFeatureFlags =
    canResolvePostLogin && !requiredFlagsEnabled && !featureFlagsReady;

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

  const destination = useMemo(
    () =>
      resolvePostLoginDestination({
        currentPath: resolvedCurrentPath,
        returnTo,
        user,
        deploymentMode: mode,
        fallbackDestination,
        flags,
        activationState,
        activationStateError,
      }),
    [
      activationState,
      activationStateError,
      fallbackDestination,
      flags,
      mode,
      resolvedCurrentPath,
      returnTo,
      user,
    ],
  );

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
