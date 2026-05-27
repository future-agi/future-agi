import { paths } from "src/routes/paths";

export const POST_LOGIN_REQUIRED_FLAGS = Object.freeze([
  "onboarding_activation_state_api",
  "onboarding_first_run_home",
  "onboarding_release_0_internal",
]);

export const POST_LOGIN_HOME_STAGES = Object.freeze([
  "choose_goal",
  "connect_observability",
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
  "review_first_trace",
  "first_loop_complete",
  "activated",
  "permission_limited",
]);

const AUTH_ROUTE_PREFIXES = Object.freeze([
  "/auth",
  "/login",
  "/logout",
  "/callback",
]);

const trimTrailingSlash = (value) => {
  if (!value || value === "/") return value || "";
  return value.endsWith("/") ? value.slice(0, -1) : value;
};

export const routePathname = (href) => {
  if (!href || typeof href !== "string") return "";
  const [withoutHash] = href.split("#");
  const [pathname] = withoutHash.split("?");
  return trimTrailingSlash(pathname || "/");
};

const comparableRoute = (href) => {
  if (!href || typeof href !== "string") return "";
  const [withoutHash] = href.split("#");
  const [pathname, query] = withoutHash.split("?");
  const normalizedPath = trimTrailingSlash(pathname || "/");
  return query ? `${normalizedPath}?${query}` : normalizedPath;
};

const routesMatch = (left, right) =>
  comparableRoute(left) === comparableRoute(right);

export const routeForAnalytics = (href) => routePathname(href);

export const isSafePostLoginReturnTo = (returnTo) => {
  if (!returnTo || typeof returnTo !== "string") return false;
  if (!returnTo.startsWith("/") || returnTo.startsWith("//")) return false;
  const pathname = routePathname(returnTo);
  return !AUTH_ROUTE_PREFIXES.some((prefix) => pathname.startsWith(prefix));
};

const hasRequiredFlags = (flags = {}) =>
  POST_LOGIN_REQUIRED_FLAGS.every((flag) => flags?.[flag] === true);

export const shouldPreserveCurrentDashboardRoute = ({
  currentPath,
  fallbackDestination,
} = {}) => {
  const pathname = routePathname(currentPath);
  if (!pathname.startsWith(paths.dashboard.root)) return false;
  if (routesMatch(pathname, paths.dashboard.root)) return false;
  if (routesMatch(pathname, paths.dashboard.home)) return false;
  if (fallbackDestination && routesMatch(pathname, fallbackDestination)) {
    return false;
  }
  return true;
};

const isWorkspaceViewer = (user = {}) =>
  user?.default_workspace_role === "workspace_viewer" ||
  user?.default_workspace_role === "Viewer";

const isPermissionLimitedHome = (activationState) =>
  activationState?.stage === "permission_limited" ||
  activationState?.permissions?.permissionLimited === true;

const isHomeEligibleStage = (stage) => POST_LOGIN_HOME_STAGES.includes(stage);

const getFallbackHref = ({ activationState, fallbackDestination }) =>
  activationState?.fallbackAction?.href ||
  activationState?.fallback_action?.href ||
  fallbackDestination;

const makeDestination = ({
  href,
  reason,
  currentPath,
  returnTo,
  usedReturnTo = false,
  fallbackDestination,
  activationState,
  activationStateError,
  shouldReplace,
}) => ({
  href,
  reason,
  shouldReplace:
    shouldReplace ?? (href ? !routesMatch(currentPath, href) : false),
  shouldClearReturnTo: Boolean(returnTo),
  usedReturnTo,
  fallbackDestination,
  source: "post_login",
  activationStage: activationState?.stage ?? null,
  activationStateError: Boolean(activationStateError),
});

export function resolvePostLoginDestination({
  currentPath,
  returnTo,
  user,
  deploymentMode,
  fallbackDestination,
  flags,
  activationState,
  activationStateError,
} = {}) {
  const fallbackHref =
    fallbackDestination ||
    (deploymentMode === "oss"
      ? paths.dashboard.develop
      : paths.dashboard.falconAI);

  if (isSafePostLoginReturnTo(returnTo)) {
    return makeDestination({
      href: returnTo,
      reason: "safe_return_to",
      currentPath,
      returnTo,
      usedReturnTo: true,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (user?.requires_org_setup) {
    return makeDestination({
      href: paths.auth.jwt.org_removed,
      reason: "requires_org_setup",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (!user?.onboarding_completed) {
    return makeDestination({
      href: currentPath || fallbackHref,
      reason: "onboarding_incomplete",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
      shouldReplace: false,
    });
  }

  if (!user?.organization_role) {
    return makeDestination({
      href: paths.dashboard.getstarted,
      reason: "no_org_role_get_started",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (
    shouldPreserveCurrentDashboardRoute({
      currentPath,
      fallbackDestination: fallbackHref,
    })
  ) {
    return makeDestination({
      href: currentPath,
      reason: "direct_dashboard_route",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
      shouldReplace: false,
    });
  }

  if (!hasRequiredFlags(flags)) {
    return makeDestination({
      href: fallbackHref,
      reason: "required_flag_off",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (activationStateError) {
    return makeDestination({
      href: fallbackHref,
      reason: "activation_state_error",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (!activationState) {
    return makeDestination({
      href: fallbackHref,
      reason: "activation_state_missing",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (activationState.stage === "feature_disabled") {
    return makeDestination({
      href: getFallbackHref({
        activationState,
        fallbackDestination: fallbackHref,
      }),
      reason: "activation_feature_disabled",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (isWorkspaceViewer(user) && !isPermissionLimitedHome(activationState)) {
    return makeDestination({
      href: fallbackHref,
      reason: "workspace_viewer_fallback",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (
    activationState.isActivated === true &&
    flags?.onboarding_daily_quality_home === true
  ) {
    return makeDestination({
      href: `${paths.dashboard.home}?mode=daily-quality`,
      reason: "daily_quality_home",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  if (isHomeEligibleStage(activationState.stage)) {
    return makeDestination({
      href: paths.dashboard.home,
      reason: "internal_onboarding_home",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
    });
  }

  return makeDestination({
    href: fallbackHref,
    reason: "activation_stage_not_eligible",
    currentPath,
    returnTo,
    fallbackDestination: fallbackHref,
    activationState,
    activationStateError,
  });
}
