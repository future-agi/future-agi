import mixpanel from "mixpanel-browser";
import { matchPath } from "react-router";

import logger from "../logger";

import { Events } from "./EventNames";
import { MIXPANEL_HOST } from "src/config-global";

// Mixpanel is opt-in: skip init entirely on OSS builds where no token
// is set. mixpanel-browser's init(undefined, ...) silently produces a
// half-broken object that throws on every track() — flooding the console
// with "Cannot read properties of undefined (reading 'disable_all_events')".
const MIXPANEL_TOKEN = import.meta.env.VITE_MIXPANEL_TOKEN;
const MIXPANEL_ENABLED = Boolean(MIXPANEL_TOKEN);

if (MIXPANEL_ENABLED) {
  try {
    mixpanel.init(MIXPANEL_TOKEN, {
      debug: true,
      persistence: "localStorage",
      ignore_dnt: true,
      record_sessions_percent: 100,
      record_mask_text_class: ".sensitive",
      ...(MIXPANEL_HOST && { api_host: MIXPANEL_HOST }),
    });
  } catch (error) {
    logger.error("Failed to initialize Mixpanel:", error);
  }
}

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

export const trackEvent = (eventName, properties) => {
  if (!MIXPANEL_ENABLED) return;
  try {
    mixpanel.track(eventName, properties);
  } catch (error) {
    logger.error("Failed to track event:", error);
  }
};

export const trackError = (properties, where) => {
  if (!MIXPANEL_ENABLED) return;
  try {
    mixpanel.track(
      `Client Error : ${where || window.location.pathname}`,
      properties,
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
    // Mixpanel identification
    mixpanel.identify(id);
    mixpanel.people.set_once("id", id);
    mixpanel.people.set_once("$email", email);
    mixpanel.people.set_once("$name", userData?.name);
    mixpanel.people.set_once("Org Id", userData?.organization?.id);
    mixpanel.people.set_once("Org Name", userData?.organization?.name);
    mixpanel.people.set_once(
      "Workspace Id",
      userData?.default_workspace_id ?? userData?.defaultWorkspaceId,
    );

    //set group
    mixpanel.set_group("org_id", userData?.organization?.id);

    mixpanel.get_group("org_id", userData?.organization?.id).set_once({
      "Organization Name": userData?.organization?.name,
    });

    // Appcues identification
    if (window.Appcues) {
      window.Appcues.identify(id, {
        email: email,
      });
    }
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
  if (!MIXPANEL_ENABLED) {
    if (window.Appcues) window.Appcues.reset();
    return;
  }
  try {
    mixpanel.reset();
    if (window.Appcues) {
      window.Appcues.reset();
    }
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
