import mixpanel from "mixpanel-browser";
import { matchPath } from "react-router";

import logger from "../logger";

import { Events } from "./EventNames";
import { MIXPANEL_HOST } from "src/config-global";
import { isSessionReplayBlockedPath } from "src/utils/sessionReplayPolicy";
import { scrubAnalyticsProperties } from "src/utils/analytics-scrub";
import { analyticsUrl, pageViewPath } from "src/utils/analytics-url";

export { pageViewPath };

// Mixpanel is opt-in: skip init entirely on OSS builds where no token
// is set. mixpanel-browser's init(undefined, ...) silently produces a
// half-broken object that throws on every track() — flooding the console
// with "Cannot read properties of undefined (reading 'disable_all_events')".
const MIXPANEL_TOKEN = import.meta.env.VITE_MIXPANEL_TOKEN;
const MIXPANEL_ENABLED = Boolean(MIXPANEL_TOKEN);
const runtimeSessionReplayPercent =
  typeof window === "undefined"
    ? undefined
    : window.__FUTURE_AGI_CONFIG__?.VITE_MIXPANEL_SESSION_REPLAY_PERCENT;
const configuredSessionReplayPercent = Number(
  runtimeSessionReplayPercent !== undefined &&
    String(runtimeSessionReplayPercent).trim() !== ""
    ? runtimeSessionReplayPercent
    : import.meta.env.VITE_MIXPANEL_SESSION_REPLAY_PERCENT ?? 0,
);
const MIXPANEL_SESSION_REPLAY_PERCENT = Number.isFinite(
  configuredSessionReplayPercent,
)
  ? Math.min(100, Math.max(0, configuredSessionReplayPercent))
  : 0;
const INITIAL_PATHNAME =
  typeof window === "undefined" ? "/" : window.location.pathname;
let mixpanelReplayMayBeActive =
  MIXPANEL_SESSION_REPLAY_PERCENT > 0 &&
  !isSessionReplayBlockedPath(INITIAL_PATHNAME);
let mixpanelReplayConfigDisabled = false;
let mixpanelReplayStopScript = null;

// mixpanel-browser adds the page address ($current_url), document.referrer
// ($referrer) and the first referrer it stored ($initial_referrer) to every
// event. These are reported as analyticsUrl gives them: link codes replaced by
// the route pattern, one-time query parameters removed.
const URL_PROPERTIES = ["$current_url", "$referrer", "$initial_referrer"];

// Cleans top-level text values that are URLs just before sending: the URL
// properties above and values the library collects itself, such as campaign
// (utm_*) parameters. analyticsUrl leaves text that is not a URL, and URLs
// without a link code or one-time parameter, as they are.
const cleanUrlProperties = (properties, isUrlProperty = () => true) => {
  if (!properties || typeof properties !== "object") return;
  Object.keys(properties).forEach((name) => {
    if (typeof properties[name] === "string" && isUrlProperty(name)) {
      properties[name] = analyticsUrl(properties[name]);
    }
  });
};

// Profile properties the library adds itself: the first referrer and the
// first campaign values. Profile properties the app sets, such as names, are
// sent as they are.
const isLibraryProfileUrlProperty = (name) =>
  name === "$initial_referrer" || name.startsWith("initial_utm_");

// The values the library would add to an event now, where they differ from
// what analyticsUrl reports. Empty on ordinary pages.
const urlPropertyOverrides = () => {
  const current = {
    $current_url: window.location.href,
    $referrer: document.referrer,
    $initial_referrer: mixpanel.get_property?.("$initial_referrer"),
  };
  const overrides = {};
  URL_PROPERTIES.forEach((name) => {
    const cleaned = analyticsUrl(current[name]);
    if (cleaned !== current[name]) overrides[name] = cleaned;
  });
  return overrides;
};

// Caller properties are merged after the library's own, so these replace the
// library's values for this event.
const withUrlOverrides = (properties) => {
  const overrides = urlPropertyOverrides();
  return Object.keys(overrides).length
    ? { ...properties, ...overrides }
    : properties;
};

// The library stores the first referrer once, as is, and sends it with later
// events and the user profile. Store it as analyticsUrl reports it instead,
// and clean a value stored earlier.
const storeCleanReferrer = (instance) => {
  instance.persistence?.update_referrer_info?.(analyticsUrl(document.referrer));
  const stored = instance.get_property?.("$initial_referrer");
  const cleaned = analyticsUrl(stored);
  if (cleaned !== stored) instance.register({ $initial_referrer: cleaned });
};

if (MIXPANEL_ENABLED) {
  try {
    mixpanel.init(MIXPANEL_TOKEN, {
      debug: true,
      persistence: "localStorage",
      ignore_dnt: true,
      // Product analytics events remain enabled. Session replay is intentionally
      // opt-in because multiple rrweb recorders retained hundreds of MiB on
      // mutation-heavy grids. High-mutation routes are blocked even when an
      // environment explicitly opts into replay.
      record_sessions_percent: mixpanelReplayMayBeActive
        ? MIXPANEL_SESSION_REPLAY_PERCENT
        : 0,
      record_mask_text_class: ".sensitive",
      // Page views are tracked by the app through trackEvent.
      track_pageview: false,
      loaded: storeCleanReferrer,
      // Run just before each request is sent, also for events the library
      // sends itself (such as $identify) and for profile updates.
      hooks: {
        before_send_events: (data) => {
          cleanUrlProperties(data?.properties);
          return data;
        },
        before_send_people: (data) => {
          cleanUrlProperties(data?.$set, isLibraryProfileUrlProperty);
          cleanUrlProperties(data?.$set_once, isLibraryProfileUrlProperty);
          return data;
        },
      },
      ...(MIXPANEL_HOST && { api_host: MIXPANEL_HOST }),
    });
  } catch (error) {
    logger.error("Failed to initialize Mixpanel:", error);
  }
}

export const syncMixpanelSessionReplay = (pathname) => {
  if (
    !MIXPANEL_ENABLED ||
    !mixpanelReplayMayBeActive ||
    !isSessionReplayBlockedPath(pathname)
  ) {
    return;
  }

  try {
    // Keep each successful transition, but leave replay retryable until every
    // required operation has succeeded. In particular, do not clear
    // mixpanelReplayMayBeActive before a throwing set_config/stop call.
    if (!mixpanelReplayConfigDisabled) {
      mixpanel.set_config({ record_sessions_percent: 0 });
      mixpanelReplayConfigDisabled = true;
    }

    if (mixpanel._recorder) {
      mixpanel.stop_session_recording();
      mixpanelReplayMayBeActive = false;
      mixpanelReplayStopScript = null;
      return;
    }

    // The recorder bundle may still be loading. Stop it immediately after its
    // existing script load handler starts the recorder.
    const recorderSource = mixpanel.get_config?.("recorder_src");
    let normalizedRecorderSource = null;
    if (recorderSource) {
      try {
        normalizedRecorderSource = new URL(recorderSource, document.baseURI)
          .href;
      } catch (error) {
        logger.error("Failed to resolve Mixpanel recorder source:", error);
      }
    }
    const recorderScript = normalizedRecorderSource
      ? Array.from(document.scripts).find(
          (script) => script.src === normalizedRecorderSource,
        )
      : null;

    // A successful config update is sufficient when this browser was not
    // sampled and no recorder load is in flight.
    if (!recorderScript) {
      mixpanelReplayMayBeActive = false;
      return;
    }
    if (mixpanelReplayStopScript === recorderScript) return;

    const stopLoadedRecorder = () => {
      if (mixpanelReplayStopScript === recorderScript) {
        mixpanelReplayStopScript = null;
      }
      try {
        if (mixpanel._recorder) mixpanel.stop_session_recording();
        mixpanelReplayMayBeActive = false;
      } catch (error) {
        // The one-shot listener has been consumed. Keep replay eligible for a
        // direct retry on the next blocked-route synchronization.
        logger.error("Failed to stop Mixpanel session replay:", error);
      }
    };
    recorderScript.addEventListener("load", stopLoadedRecorder, { once: true });
    mixpanelReplayStopScript = recorderScript;
  } catch (error) {
    logger.error("Failed to stop Mixpanel session replay:", error);
  }
};

export const PropertyName = {
  method: "method",
  formFields: "form_fields",
  email: "email",
  email_list: "email list",
  status: "status",
  error: "error",
  errorDetails: "error_details",
  name: "name",
  click: "click",
  count: "count",
  propId: "prop_id",
  originalName: "original_name",
  newName: "new_name",
  derivedDataset: "derived_dataset",
  column: "column",
  id: "id",
  meta: "meta",
  newColumn: "new_column",
  duplicateRow: "duplicate_row",
  rowToNewDataset: "row_to_new_dataset",
  rowToExistingDataset: "row_to_existing_dataset",
  deleteRow: "delete_row",
  rowEval: "row_eval",
  columnName: "column_name",
  columnType: "column_type",
  type: "type",
  expRowDelete: "exp_row_delete",
  languageUsed: "language_used",
  description: "desc",
  pattern: "pattern",
  useCase: "use_case",
  propName: "prop_name",
  propDesc: "prop_desc",
  subset: "subset",
  split: "split",
  addSelectedRows: "add_selected_rows",
  rowCount: "row_count",
  columnCount: "column_count",
  fileName: "file_name",
  promptId: "prompt_id",
  evalId: "eval_id",
  evalName: "Eval name",
  evalType: "eval_type",
  searchTerm: "search_term",
  mode: "mode",
  version: "version",
  model: "model",
  errorLocalizerState: "Error localizer state",
  source: "source",
  datasetId: "dataset_id",
  rowIdentifier: "row_identifier",
  metric: "metric",
  graphType: "graph_type",
  concurrency: "concurrency",
  promptTemplate: "prompt_template",
  toolConfig: "tools_config",
  modelName: "model_name",
  modelOptions: "model_options",
  list: "list",
  toggle: "toggle",
  category: "category",
  currentLocation: "current_location",
  newLocation: "new_location",
  role: "role",
  goals: "goals",
};

const CUSTOM_MODEL_PROVIDERS = new Set([
  "azure",
  "openai",
  "vertex_ai",
  "custom",
  "bedrock",
  "sagemaker",
]);

// Custom-model events use a fixed property set: the provider (or "other") and
// whether it was a create or an edit.
const customModelEventProperties = (properties) => ({
  model_provider: CUSTOM_MODEL_PROVIDERS.has(properties?.model_provider)
    ? properties.model_provider
    : "other",
  operation: properties?.operation === "edit" ? "edit" : "create",
});

export const trackEvent = (eventName, properties) => {
  if (!MIXPANEL_ENABLED) return;
  try {
    mixpanel.track(
      eventName,
      withUrlOverrides(
        eventName === Events.customModelAdded
          ? customModelEventProperties(properties)
          : scrubAnalyticsProperties(properties),
      ),
    );
  } catch (error) {
    logger.error("Failed to track event:", error);
  }
};

export const trackError = (properties, where) => {
  if (!MIXPANEL_ENABLED) return;
  try {
    mixpanel.track(
      `Client Error : ${
        where
          ? analyticsUrl(String(where))
          : pageViewPath(window.location.pathname)
      }`,
      withUrlOverrides(scrubAnalyticsProperties(properties)),
    );
  } catch (error) {
    logger.error("Failed to track error:", error);
  }
};

export const identifyUser = (userData = {}) => {
  const { id, email } = userData;
  logger.debug("identifyUser userData", userData);
  if (!id) {
    logger.error("User ID is required for identification");
    return;
  }
  if (!MIXPANEL_ENABLED) return;

  try {
    // Mixpanel identification. identify() can send an $identify event; when
    // requests are not queued it is sent without the before_send hooks, so the
    // cleaned URL properties are registered for that call only.
    const overrides = urlPropertyOverrides();
    const overrideNames = Object.keys(overrides);
    if (overrideNames.length) {
      mixpanel.register(overrides, { persistent: false });
    }
    try {
      mixpanel.identify(id);
    } finally {
      overrideNames.forEach((name) =>
        mixpanel.unregister(name, { persistent: false }),
      );
    }
    mixpanel.people.set_once("id", id);
    mixpanel.people.set_once("$email", email);
    mixpanel.people.set_once("$name", userData?.name);
    mixpanel.people.set_once("Org Id", userData?.organization?.id);
    mixpanel.people.set_once("Org Name", userData?.organization?.name);
    mixpanel.people.set_once("Workspace Id", userData?.default_workspace_id);

    //set group
    mixpanel.set_group("org_id", userData?.organization?.id);

    mixpanel.get_group("org_id", userData?.organization?.id).set_once({
      "Organization Name": userData?.organization?.name,
    });
  } catch (error) {
    logger.error("Failed to identify user:", error);
    trackError({
      error: error.message,
      userId: id,
      email: email,
      context: "identifyUser",
    });
  }
};

export const resetUser = () => {
  if (!MIXPANEL_ENABLED) return;
  try {
    mixpanel.reset();
    // reset() clears the stored first referrer; store it again cleaned.
    storeCleanReferrer(mixpanel);
  } catch (error) {
    logger.error("Failed to reset user:", error);
  }
};

const pathToEventMapper = {
  "/dashboard/projects": Events.projectLandingPageVisited,
  "/dashboard/prototype": Events.projectListsPageVisited,
  "/dashboard/observe": Events.projectListsPageVisited,
  "/auth/jwt/login": Events.signInPageVisited,
  "/auth/jwt/register": Events.signUpPageVisited,
  "/auth/jwt/forget-password": Events.forgotPassPageVisited,
  "/auth/jwt/setup-org": Events.setupOrgPageVisited,
  "/dashboard/prompt": Events.promptManagementPageVisited,
  "/dashboard/develop": Events.buildPageVisited,
  "/dashboard/prompt/add": Events.promptFromScratchVisited,
  "/dashboard/settings/profile-settings": Events.profilePageVisited,
  "/dashboard/settings/api_keys": Events.apiPageVisited,
  "/dashboard/settings/pricing": Events.plansPricingVisited,
  "/dashboard/settings/user-management": Events.userManagementPageVisited,
  "/dashboard/settings/billing": Events.billingVisited,
  "/dashboard/settings/custom-model": Events.customModelPageVisited,
  "/dashboard/settings/usage-summary": Events.usageManagementVisited,
  "/dashboard/settings/ai-providers": Events.customModelPageVisited,
  "/dashboard/workbench": Events.promptManagementPageVisited,
  "/dashboard/keys": Events.apiPageVisited,
};

export const getPageViewEvent = (currentPath) => {
  const cleanedPath = currentPath.replace(/\/$/, "");

  const buildEvent = (event, extras = {}) => ({
    eventName: event,
    extras: {
      ...extras,
    },
  });

  if (currentPath.startsWith("/dashboard/prototype")) {
    if (matchPath("/dashboard/prototype/:projectId/:runId", currentPath)) {
      return buildEvent(Events.insideRunsPageVisited);
    }
    if (matchPath("/dashboard/prototype/:projectId", currentPath)) {
      return buildEvent(Events.insideProjectLandingPageVisited);
    }
  }

  if (currentPath.startsWith("/dashboard/observe")) {
    if (matchPath("/dashboard/observe/:observeId/llm-tracing", currentPath)) {
      return buildEvent(Events.pObserveInsideProjectLandingPageVisited);
    }
    if (
      matchPath("/dashboard/observe/:observeId/project-settings", currentPath)
    ) {
      return buildEvent(Events.projectSettingsPageVisited);
    }
    if (matchPath("/dashboard/observe/:observeId/charts", currentPath)) {
      return buildEvent(Events.chartsPageVisited);
    }
    if (matchPath("/dashboard/observe/:observeId/alerts", currentPath)) {
      return buildEvent(Events.alertHomepageLoaded, {
        [PropertyName.source]: "observe_tab",
      });
    }
  }

  if (matchPath("/dashboard/prompt/add/:id", currentPath)) {
    return buildEvent(Events.promptFromScratchVisoted);
  }

  if (matchPath("/auth/jwt/verify/:token/:code", currentPath)) {
    return buildEvent(Events.changePassPageVisitied);
  }

  if (matchPath("/dashboard/alerts", currentPath)) {
    return buildEvent(Events.alertHomepageLoaded, {
      [PropertyName.source]: "side_navigation",
    });
  }

  const mapped = pathToEventMapper[cleanedPath];
  if (mapped) {
    return buildEvent(mapped);
  }

  return null;
};

export const handleOnDocsClicked = (source) => {
  if (!source) return;
  trackEvent(Events.docLinkClicked, {
    [PropertyName.click]: true,
    [PropertyName.source]: source,
  });
};
