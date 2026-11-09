import posthog from "posthog-js";
import logger from "../logger";
import {
  CURRENT_ENVIRONMENT,
  POSTHOG_KEY,
  POSTHOG_HOST,
} from "src/config-global";
import { SESSION_REPLAY_URL_BLOCKLIST } from "src/utils/sessionReplayPolicy";
import { scrubEmbedSecrets } from "src/sections/omega/embed/telemetry/guard";
import {
  AUTH_CREDENTIAL_PARAMS,
  hasCredentialUrl,
} from "src/sections/omega/embed/telemetry/auth-url-rules";
import {
  cleanStoredPosthogPersistence,
  preparePosthogPersistence,
} from "src/sections/omega/embed/telemetry/posthog-persistence";
import {
  POSTHOG_AUTOCAPTURE_URL_IGNORELIST,
  POSTHOG_REPLAY_URL_BLOCKLIST,
  posthogBeforeSend,
  posthogMaskNetworkRequest,
} from "src/sections/omega/embed/telemetry/sdk-guards";

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

  // Old URL copies must be cleaned before the SDK loads and re-saves them.
  if (!cleanStoredPosthogPersistence()) return;

  try {
    posthog.init(POSTHOG_KEY, {
      api_host: posthogHost,
      // 1.360.2 logs/conversations send raw URLs outside before_send. Remote
      // logs config can override its local off switch; veto their script loads.
      prepare_external_dependency_script: (script) =>
        /\/static\/(logs|conversations)\.js/.test(script.src) ? null : script,
      disable_conversations: true,
      // Share cookie across *.futureagi.com so UTM/session survives marketing → app
      cross_subdomain_cookie: true,
      cookie_domain: ".futureagi.com",
      mask_personal_data_properties: true,
      custom_personal_data_properties: [...AUTH_CREDENTIAL_PARAMS],
      // before_send does not see /flags or persistence writes. Seed clean
      // initial attribution and guard every later persistence save in loaded().
      save_referrer: false,
      save_campaign_params: false,

      // Autocapture — clicks, form submissions, pageviews. Never on pages that
      // frame an Omega surface (Studio, the Console).
      autocapture: { url_ignorelist: [...POSTHOG_AUTOCAPTURE_URL_IGNORELIST] },
      // Session replay
      session_recording: {
        // Local false wins over remote opt-in; our custom mask bypasses the
        // SDK's default payload scrubber, so never capture bodies or headers.
        recordBody: false,
        recordHeaders: false,
        maskAllInputs: true,
        maskTextSelector: "*",
        // Keep DOM/attribute payloads inspectable by before_send. Installed
        // 1.360.2 otherwise gzips them first; unexpected opaque snapshots are
        // dropped by that hook. Request-level compression can still run later.
        compress_events: false,
        maskInputOptions: {
          password: true,
        },
        // 1.360.2 reads replay blocklists from REMOTE configuration, not this
        // local option. Retain the patterns for configuration compatibility;
        // do not rely on them to suppress DOM capture. Initial credential URLs
        // are explicitly disabled below.
        urlBlocklist: [
          ...SESSION_REPLAY_URL_BLOCKLIST,
          ...POSTHOG_REPLAY_URL_BLOCKLIST,
        ],
        // Recorded network timings never include a server on this computer.
        maskCapturedNetworkRequestFn: posthogMaskNetworkRequest,
      },
      // Drops autocaptured events from frame pages and scrubs any connect
      // fragment or local-server URL from every event.
      before_send: posthogBeforeSend,
      // replaceState cannot clear the original PerformanceNavigationTiming URL.
      // In 1.360.2, false overrides remote web-vitals/network-timing opt-ins.
      // Keep maskCapturedNetworkRequestFn above: remote body/header recording
      // can still install the replay network plugin and observe timing entries.
      capture_performance: false,
      capture_pageleave: true,
      // SPA: capture pageview on history change (pushState/replaceState)
      capture_pageview: "history_change",
      // Feature flags — load on init
      advanced_disable_feature_flags: false,
      // Disable in local dev
      disable_session_recording:
        CURRENT_ENVIRONMENT === "local" ||
        (typeof window !== "undefined" &&
          hasCredentialUrl(window.location.href)),
      loaded: (ph) => {
        preparePosthogPersistence(ph);
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
    default_workspace_id,
    default_workspace_role,
    organization_role,
  } = scrubEmbedSecrets(userData);
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
      Object.keys(setOnce).length ? scrubEmbedSecrets(setOnce) : undefined,
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
  if (!initialized) return;
  try {
    posthog.capture(
      scrubEmbedSecrets(eventName),
      scrubEmbedSecrets(properties),
    );
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
