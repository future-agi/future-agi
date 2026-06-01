import { paths } from "src/routes/paths";
import { ACTIVATION_STAGE_NAMES } from "../activation-state-utils";

export const POST_LOGIN_REQUIRED_FLAGS = Object.freeze([
  "onboarding_activation_state_api",
  "onboarding_first_run_home",
  "onboarding_release_0_internal",
]);

export const POST_LOGIN_HOME_STAGES = Object.freeze(
  ACTIVATION_STAGE_NAMES.filter((stage) => stage !== "feature_disabled"),
);

const LEGACY_POST_LOGIN_FALLBACK_PATHS = Object.freeze([
  paths.dashboard.falconAI,
  paths.dashboard.getstarted,
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

const isLegacyPostLoginFallbackPath = (pathname) =>
  LEGACY_POST_LOGIN_FALLBACK_PATHS.some((legacyPath) =>
    routesMatch(pathname, legacyPath),
  );

export const isSafePostLoginReturnTo = (returnTo) => {
  if (!returnTo || typeof returnTo !== "string") return false;
  if (!returnTo.startsWith("/") || returnTo.startsWith("//")) return false;
  const pathname = routePathname(returnTo);
  if (AUTH_ROUTE_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return false;
  }
  return !isLegacyPostLoginFallbackPath(pathname);
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
  if (isLegacyPostLoginFallbackPath(pathname)) {
    return false;
  }
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

const isActivatedWorkspaceState = (activationState) =>
  activationState?.isActivated === true ||
  activationState?.stage === "activated" ||
  activationState?.stage === "daily_review";

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
  deploymentMode: _deploymentMode,
  fallbackDestination,
  flags,
  activationState,
  activationStateError,
} = {}) {
  const fallbackHref = fallbackDestination || paths.dashboard.home;
  const safeReturnTo = isSafePostLoginReturnTo(returnTo);

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
    if (
      user?.organization_role &&
      hasRequiredFlags(flags) &&
      !activationStateError &&
      isActivatedWorkspaceState(activationState)
    ) {
      if (safeReturnTo) {
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

      const href =
        flags?.onboarding_daily_quality_home === true
          ? `${paths.dashboard.home}?mode=daily-quality`
          : paths.dashboard.home;
      return makeDestination({
        href,
        reason:
          flags?.onboarding_daily_quality_home === true
            ? "daily_quality_home"
            : "workspace_setup_complete",
        currentPath,
        returnTo,
        fallbackDestination: fallbackHref,
        activationState,
        activationStateError,
      });
    }

    const href = routesMatch(currentPath, paths.auth.jwt.setup_org)
      ? currentPath
      : paths.auth.jwt.setup_org;
    return makeDestination({
      href,
      reason: "onboarding_incomplete",
      currentPath,
      returnTo,
      fallbackDestination: fallbackHref,
      activationState,
      activationStateError,
      shouldReplace: !routesMatch(currentPath, href),
    });
  }

  if (safeReturnTo) {
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

  if (!user?.organization_role) {
    return makeDestination({
      href: paths.auth.jwt.setup_org,
      reason: "no_org_role_setup_org",
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
