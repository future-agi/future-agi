import { matchPath } from "react-router";
import logger from "../logger";

import { Events } from "./EventNames";
import {
  identifyPostHogUser,
  resetPostHogUser,
  trackPostHogEvent,
} from "../PostHog";

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
  trackPostHogEvent(
    eventName,
    eventName === Events.customModelAdded
      ? customModelEventProperties(properties)
      : properties,
  );
};

export const trackError = (properties, where) => {
  trackPostHogEvent(
    `Client Error : ${where || window.location.pathname}`,
    properties,
  );
};

// Keep the existing synchronous API for callers, including axios logout paths.
// The PostHog boundary coalesces the auth provider's paired legacy/direct calls.
export const identifyUser = (userData = {}) => {
  identifyPostHogUser(userData);
  try {
    if (userData.id && window.Appcues) {
      window.Appcues.identify(userData.id, { email: userData.email });
    }
  } catch (error) {
    logger.error("Failed to identify user:", error);
    trackError({
      error: error.message,
      userId: userData.id,
      email: userData.email,
      context: "identifyUser",
    });
  }
};
export const resetUser = () => {
  resetPostHogUser();
  try {
    if (window.Appcues) window.Appcues.reset();
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
