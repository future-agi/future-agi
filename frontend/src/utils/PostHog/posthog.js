import posthog from "posthog-js";
import logger from "../logger";
import {
  CURRENT_ENVIRONMENT,
  POSTHOG_KEY,
  POSTHOG_HOST,
} from "src/config-global";

const posthogHost = POSTHOG_HOST || "https://us.i.posthog.com";
const PLACEHOLDER_POSTHOG_KEYS = new Set([
  "your_posthog_project_api_key",
  "phc_your_project_api_key",
]);
const LOCAL_SMOKE_POSTHOG_KEY_PATTERN = /^phc_local_/i;

let initialized = false;

export const isUsablePostHogKey = (key = POSTHOG_KEY) => {
  if (!key || typeof key !== "string") return false;
  const normalizedKey = key.trim();
  if (!normalizedKey) return false;
  if (PLACEHOLDER_POSTHOG_KEYS.has(normalizedKey)) return false;
  if (/^your[_-]/i.test(normalizedKey)) return false;
  if (LOCAL_SMOKE_POSTHOG_KEY_PATTERN.test(normalizedKey)) {
    return Boolean(
      typeof window !== "undefined" &&
        window.__FUTURE_AGI_ENABLE_POSTHOG_SMOKE__,
    );
  }
  return true;
};

export const initPostHog = () => {
  if (initialized || !isUsablePostHogKey()) {
    if (!isUsablePostHogKey()) {
      logger.debug(
        "PostHog: usable VITE_POSTHOG_KEY not set, skipping initialization",
      );
    }
    return;
  }

  // Browser onboarding smokes assert on the captured event payload. PostHog
  // defaults to gzip-js compression, which sends the event batch as a binary
  // blob the smoke's request interceptor cannot decode. When the smoke flag is
  // set, disable compression so the body stays plain JSON for verification.
  const disableCompressionForSmoke = Boolean(
    typeof window !== "undefined" && window.__FUTURE_AGI_ENABLE_POSTHOG_SMOKE__,
  );

  try {
    posthog.init(POSTHOG_KEY, {
      api_host: posthogHost,
      // Share cookie across *.futureagi.com so UTM/session survives marketing → app
      cross_subdomain_cookie: true,
      cookie_domain: ".futureagi.com",
      disable_compression: disableCompressionForSmoke,

      // Autocapture — clicks, form submissions, pageviews
      autocapture: true,
      // Session replay
      session_recording: {
        maskAllInputs: false,
        maskInputOptions: {
          password: true,
        },
      },
      // Capture performance / web vitals
      capture_performance: true,
      capture_pageleave: true,
      // SPA: capture pageview on history change (pushState/replaceState)
      capture_pageview: "history_change",
      // Feature flags — load on init
      advanced_disable_feature_flags: false,
      // Disable in local dev
      disable_session_recording: CURRENT_ENVIRONMENT === "local",
      loaded: (ph) => {
        // Debug mode in non-production
        if (CURRENT_ENVIRONMENT !== "production") {
          ph.debug();
        }
      },
      persistence: "localStorage+cookie",
      // Respect DNI opt-out in production, ignore in dev
      respect_dnt: CURRENT_ENVIRONMENT === "production",
    });
    initialized = true;
    logger.debug("PostHog initialized successfully");
  } catch (error) {
    logger.error("Failed to initialize PostHog:", error);
  }
};

/**
 * Identify user + set org & workspace as groups
 */
export const identifyPostHogUser = (userData = {}) => {
  if (!initialized || !isUsablePostHogKey()) return;

  const {
    id,
    email,
    name,
    organization,
    default_workspace_id,
    default_workspace_role,
    organization_role,
  } = userData;
  if (!id) return;

  try {
    const setOnce = {};
    try {
      const utmString =
        typeof window !== "undefined" &&
        window.localStorage?.getItem("utm_params");
      if (utmString) {
        const stored = new URLSearchParams(utmString);
        const utmSource = stored.get("utm_source");
        const utmMedium = stored.get("utm_medium");
        const utmCampaign = stored.get("utm_campaign");
        if (utmSource) setOnce.$initial_utm_source = utmSource;
        if (utmMedium) setOnce.$initial_utm_medium = utmMedium;
        if (utmCampaign) setOnce.$initial_utm_campaign = utmCampaign;
      }
    } catch (storageError) {
      logger.debug(
        "PostHog: could not read utm_params from storage",
        storageError,
      );
    }

    // Identify user
    posthog.identify(
      id,
      {
        email,
        name,
        workspace_id: default_workspace_id,
        workspace_role: default_workspace_role,
        organization_role,
      },
      Object.keys(setOnce).length ? setOnce : undefined,
    );

    // Group: Organization (type 0)
    if (organization?.id) {
      posthog.group("organization", organization.id, {
        name: organization.name || organization.display_name,
      });
    }

    // Group: Workspace (type 1)
    if (default_workspace_id) {
      posthog.group("workspace", default_workspace_id, {
        organization_id: organization?.id,
      });
    }
  } catch (error) {
    logger.error("PostHog identify error:", error);
  }
};

/**
 * Reset on logout
 */
export const resetPostHogUser = () => {
  if (!initialized) return;
  try {
    posthog.reset();
  } catch (error) {
    logger.error("PostHog reset error:", error);
  }
};

/**
 * Track custom event (for the few events you do want to track explicitly)
 */
export const trackPostHogEvent = (eventName, properties = {}) => {
  if (!initialized || !isUsablePostHogKey()) return;
  try {
    posthog.capture(eventName, properties);
  } catch (error) {
    logger.error("PostHog track error:", error);
  }
};

/**
 * Feature flag check
 */
export const isFeatureEnabled = (flagName) => {
  if (!initialized) return false;
  try {
    return posthog.isFeatureEnabled(flagName);
  } catch (error) {
    logger.error("PostHog feature flag error:", error);
    return false;
  }
};

export const isPostHogAvailable = () => initialized;

export const getFeatureFlagValue = (flagName) => {
  if (!initialized) return undefined;
  try {
    return posthog.getFeatureFlag(flagName);
  } catch (error) {
    logger.error("PostHog feature flag error:", error);
    return undefined;
  }
};

/**
 * Get feature flag payload (for multivariate flags)
 */
export const getFeatureFlagPayload = (flagName) => {
  if (!initialized) return null;
  try {
    return posthog.getFeatureFlagPayload(flagName);
  } catch (error) {
    logger.error("PostHog feature flag payload error:", error);
    return null;
  }
};

/**
 * Callback when feature flags are loaded
 */
export const onFeatureFlags = (callback) => {
  if (!initialized) return undefined;
  return posthog.onFeatureFlags(callback);
};

export { posthog };
