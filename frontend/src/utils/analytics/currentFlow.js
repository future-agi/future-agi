import { trackPostHogEvent } from "src/utils/PostHog";

export const CurrentFlowEvents = {
  currentFlowPostLoginDecisionResolved:
    "current_flow_post_login_decision_resolved",
  currentFlowFirstLandingViewed: "current_flow_first_landing_viewed",
  currentFlowFirstProductRouteOpened: "current_flow_first_product_route_opened",
  currentFlowGetStartedViewed: "current_flow_get_started_viewed",
  currentFlowGetStartedTabsLoaded: "current_flow_get_started_tabs_loaded",
  currentFlowGetStartedStepSelected: "current_flow_get_started_step_selected",
  currentFlowGetStartedPrimaryClicked:
    "current_flow_get_started_primary_clicked",
  currentFlowTryFeatureClicked: "current_flow_try_feature_clicked",
  currentFlowFalconViewed: "current_flow_falcon_viewed",
  currentFlowFalconMessageSent: "current_flow_falcon_message_sent",
  currentFlowFirstValueCandidate: "current_flow_first_value_candidate",
};

const BLOCKED_PROPERTY_KEYS = new Set([
  "email",
  "message",
  "message_text",
  "prompt",
  "prompt_text",
  "trace_payload",
  "model_response",
  "api_key",
  "provider_key",
  "secret",
]);

const PRODUCT_ROUTE_PREFIXES = [
  ["/dashboard/develop", "dataset"],
  ["/dashboard/prompt", "prompt"],
  ["/dashboard/workbench", "prompt"],
  ["/dashboard/prototype", "experiment"],
  ["/dashboard/agents", "agent"],
  ["/dashboard/simulate", "agent"],
  ["/dashboard/observe", "observe"],
  ["/dashboard/gateway", "gateway"],
  ["/dashboard/evaluations", "evals"],
  ["/dashboard/error-feed", "observe"],
  ["/dashboard/dashboards", "dashboards"],
  ["/dashboard/annotation", "annotation"],
  ["/dashboard/settings", "settings"],
  ["/dashboard/keys", "settings"],
];

export const normalizeCurrentFlowProperties = (properties = {}) =>
  Object.entries(properties).reduce((result, [key, value]) => {
    if (value === undefined || BLOCKED_PROPERTY_KEYS.has(key)) {
      return result;
    }
    result[key] = value;
    return result;
  }, {});

export const markOncePerWorkspaceSession = (keyParts = []) => {
  if (typeof window === "undefined" || !window.sessionStorage) return true;

  const key = ["currentFlow", ...keyParts.map((part) => part ?? "unknown")]
    .join(":")
    .replace(/\s+/g, "_");

  try {
    if (window.sessionStorage.getItem(key)) return false;
    window.sessionStorage.setItem(key, "1");
    return true;
  } catch {
    return true;
  }
};

export const getRouteFamily = (pathname = "") => {
  if (pathname.startsWith("/dashboard/falcon-ai")) return "falcon";
  if (pathname.startsWith("/dashboard/get-started")) return "get_started";
  if (pathname === "/dashboard" || pathname === "/dashboard/") {
    return "dashboard";
  }

  const route = PRODUCT_ROUTE_PREFIXES.find(([prefix]) =>
    pathname.startsWith(prefix),
  );

  return route?.[1] || "other";
};

export const isOnboardingRoute = (pathname = "") =>
  pathname.startsWith("/dashboard/get-started");

export const isProductRoute = (pathname = "") => {
  const family = getRouteFamily(pathname);
  return !["falcon", "get_started", "dashboard", "other"].includes(family);
};

export const normalizeGetStartedStep = (label) => {
  if (!label) return null;
  return String(label)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[^a-zA-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
};

export const getFirstIncompleteStep = (firstChecks = {}) => {
  const entry = Object.entries(firstChecks).find(([, value]) => !value);
  return entry?.[0] || null;
};

export const buildCurrentFlowContext = ({
  user,
  route,
  postLoginPath,
  deploymentMode,
} = {}) => {
  const currentRoute =
    route ||
    (typeof window !== "undefined" ? window.location.pathname : undefined);

  return normalizeCurrentFlowProperties({
    workspace_id: user?.default_workspace_id,
    organization_id: user?.organization?.id,
    user_id: user?.id,
    route: currentRoute,
    route_family: currentRoute ? getRouteFamily(currentRoute) : undefined,
    deployment_mode: deploymentMode,
    post_login_path: postLoginPath,
    is_invited_user: Boolean(user?.invited_by || user?.invite_id),
    organization_role: user?.organization_role ?? user?.organizationRole,
    workspace_role: user?.default_workspace_role,
    onboarding_completed: user?.onboarding_completed,
    requires_org_setup: user?.requires_org_setup,
  });
};

export const trackCurrentFlow = (eventName, properties = {}, options = {}) => {
  if (!eventName) return false;

  if (
    options.onceKeyParts &&
    !markOncePerWorkspaceSession(options.onceKeyParts)
  ) {
    return false;
  }

  const safeProperties = normalizeCurrentFlowProperties(properties);
  trackPostHogEvent(eventName, safeProperties);
  return true;
};
