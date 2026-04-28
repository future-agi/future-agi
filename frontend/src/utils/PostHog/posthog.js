import posthog from "posthog-js";
import logger from "../logger";
import {
  CURRENT_ENVIRONMENT,
  POSTHOG_KEY,
  POSTHOG_HOST,
} from "src/config-global";

const posthogHost = POSTHOG_HOST || "https://us.i.posthog.com";

let initialized = false;

export const initPostHog = () => {
  if (initialized || !POSTHOG_KEY) {
    if (!POSTHOG_KEY) {
      logger.debug(
        "PostHog: VITE_POSTHOG_KEY not set, skipping initialization",
      );
    }
    return;
  }

  try {
    posthog.init(POSTHOG_KEY, {
      api_host: posthogHost,
      // Share cookie across *.futureagi.com so UTM/session survives marketing → app
      cross_subdomain_cookie: true,
      cookie_domain:'.futureagi.com',
      
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
  if (!initialized || !POSTHOG_KEY) return;

  const {
    id,
    email,
    name,
    organization,
    defaultWorkspaceId,
    defaultWorkspaceRole,
    organizationRole,
  } = userData;
  if (!id) return;

  try {
    // Identify user
    posthog.identify(id, {
      email,
      name,
      workspace_id: defaultWorkspaceId,
      workspace_role: defaultWorkspaceRole,
      organization_role: organizationRole,
    });

    // Group: Organization (type 0)
    if (organization?.id) {
      posthog.group("organization", organization.id, {
        name: organization.name || organization.display_name,
      });
    }

    // Group: Workspace (type 1)
    if (defaultWorkspaceId) {
      posthog.group("workspace", defaultWorkspaceId, {
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
  if (!initialized) return;
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
  if (!initialized) return;
  posthog.onFeatureFlags(callback);
};

export { posthog };
