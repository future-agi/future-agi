import axios from "axios";
import { enqueueSnackbar } from "notistack";
import {
  addToQueue,
  clearTokens,
  getIsRefreshing,
  getRefreshToken,
  getRememberMe,
  processQueue,
  refreshTokenRequest,
  setIsRefreshing,
  setSession,
} from "src/auth/context/jwt/utils";
import { HOST_API } from "src/config-global";
import { apiPath, legacyApiPath } from "src/api/contracts/api-surface";
import {
  assertContractedRequestConfig,
  assertContractedResponse,
} from "src/api/contracts/openapi-contract";
import { resetUser } from "./Mixpanel";
import logger from "./logger";
import { RESPONSE_CODES } from "./constants";

// ----------------------------------------------------------------------
//
const axiosInstance = axios.create({ baseURL: HOST_API });

// ----------------------------------------------------------------------
// Compatibility bridge: backend responses are now snake_case, but a lot of
// existing UI code still reads camelCase keys (`columnConfig`, `rowId`,
// `totalRows`, etc.). Add camelCase aliases on responses so those flows keep
// working while new dynamic-field lists can use canonicalKeys/canonicalEntries
// to avoid showing both forms.
// ----------------------------------------------------------------------
const SNAKE_TO_CAMEL_RE = /_([a-z0-9])/g;

function snakeToCamelKey(key) {
  return key.replace(SNAKE_TO_CAMEL_RE, (_, c) => c.toUpperCase());
}

const USER_KEYED_MAP_FIELDS = new Set([
  "variable_names",
  "mapping",
  "placeholders",
  "params",
  "headers",
  "choice_scores",
  "attributes",
  "span_attributes",
  "trace_attributes",
  "session_attributes",
  "call_attributes",
  "voice_call_attributes",
]);

function buildAliasTable(obj) {
  const table = {};
  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i];
    if (!key.includes("_")) continue;
    const camel = snakeToCamelKey(key);
    if (camel !== key && !(camel in obj)) {
      table[camel] = key;
    }
  }
  return table;
}

function isSpecialObject(obj) {
  return (
    obj instanceof Date ||
    obj instanceof RegExp ||
    (typeof FormData !== "undefined" && obj instanceof FormData) ||
    (typeof Blob !== "undefined" && obj instanceof Blob) ||
    (typeof File !== "undefined" && obj instanceof File)
  );
}

function addCamelAliases(obj, seen) {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== "object") return obj;
  if (seen.has(obj)) return obj;
  seen.add(obj);

  if (isSpecialObject(obj)) return obj;

  if (Array.isArray(obj)) {
    for (let i = 0; i < obj.length; i += 1) {
      addCamelAliases(obj[i], seen);
    }
    return obj;
  }

  const originalKeys = Object.keys(obj);
  for (let i = 0; i < originalKeys.length; i += 1) {
    const key = originalKeys[i];
    if (USER_KEYED_MAP_FIELDS.has(key)) continue;
    const value = obj[key];
    if (value !== null && typeof value === "object") {
      addCamelAliases(value, seen);
    }
  }

  // Keep aliases enumerable because many legacy call sites spread API objects
  // before reading camelCase keys. Use canonicalKeys/canonicalEntries anywhere
  // object keys are rendered to users.
  const aliases = buildAliasTable(obj);
  Object.keys(aliases).forEach((camel) => {
    const snake = aliases[camel];
    try {
      obj[camel] = obj[snake];
    } catch {
      // Ignore read-only / frozen objects.
    }
  });

  return obj;
}

function stripCamelAliases(obj, seen) {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== "object") return obj;
  if (seen.has(obj)) return obj;
  seen.add(obj);

  if (isSpecialObject(obj)) return obj;

  if (Array.isArray(obj)) {
    for (let i = 0; i < obj.length; i += 1) {
      stripCamelAliases(obj[i], seen);
    }
    return obj;
  }

  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i];
    const value = obj[key];
    if (value !== null && typeof value === "object") {
      stripCamelAliases(value, seen);
    }
    if (/[A-Z]/.test(key) && !key.includes("_")) {
      const snakeKey = key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
      if (
        snakeKey !== key &&
        Object.prototype.hasOwnProperty.call(obj, snakeKey) &&
        obj[snakeKey] === obj[key]
      ) {
        try {
          delete obj[key];
        } catch {
          // Ignore non-configurable objects.
        }
      }
    }
  }

  return obj;
}

const avoidRedirect = [
  "/auth/jwt/register",
  "/auth/jwt/login",
  "/auth/jwt/forget-password",
  "/auth/jwt/invitation/accept/",
  "/auth/jwt/invitation/set-password/",
  "/auth/jwt/verify/",
  "/mcp/authorize",
  "/auth/jwt/two-factor",
  "/auth/jwt/org-removed",
];

axiosInstance.interceptors.request.use((config) => {
  try {
    if (
      config?.data &&
      typeof config.data === "object" &&
      !(typeof FormData !== "undefined" && config.data instanceof FormData) &&
      !(typeof Blob !== "undefined" && config.data instanceof Blob)
    ) {
      try {
        config.data = structuredClone(config.data);
      } catch {
        try {
          config.data = JSON.parse(JSON.stringify(config.data));
        } catch {
          return config;
        }
      }
      stripCamelAliases(config.data, new WeakSet());
    }
  } catch {
    // Never break a request because of response-shape compatibility cleanup.
  }
  return assertContractedRequestConfig(config);
});

axiosInstance.interceptors.response.use(
  (res) => {
    const validatedResponse = assertContractedResponse(res);
    try {
      if (validatedResponse?.data) {
        addCamelAliases(validatedResponse.data, new WeakSet());
      }
    } catch {
      // Never break a successful response because of compatibility aliases.
    }
    return validatedResponse;
  },
  async (error) => {
    const currentPath = window.location.href;
    const avoid = avoidRedirect.some((item) => currentPath.includes(item));
    const originalRequest = error?.config;
    const status = error?.response?.status;
    const url = error.config?.url;
    const authEndpoints = [
      "/accounts/user-info/",
      "/accounts/token/",
      "/accounts/logout/",
    ];

    // Handle 403 with 2fa_required code — org enforcement
    if (
      status === RESPONSE_CODES.FORBIDDEN &&
      error?.response?.data?.code === "2fa_required"
    ) {
      window.dispatchEvent(
        new CustomEvent("2fa-enforcement-block", {
          detail: error.response.data,
        }),
      );
    }

    // Handle 402 Payment Required — EE feature unavailable on OSS. Surface
    // the backend-provided message via the shared snackbar so every EE
    // endpoint gets consistent UX without each caller having to handle it.
    if (
      status === RESPONSE_CODES.PAYMENT_REQUIRED &&
      error?.response?.data?.upgrade_required
    ) {
      const upgradeError = error.response.data.error;
      enqueueSnackbar(
        (typeof upgradeError === "string"
          ? upgradeError
          : upgradeError?.message) ||
          "Not available on OSS. Upgrade your plan.",
        { variant: "error" },
      );
    }

    // Handle 401 and try refresh
    if (status === RESPONSE_CODES.UNAUTHORIZED && !originalRequest?._retry) {
      const refreshToken = getRefreshToken();
      const rememberMe = getRememberMe();

      // 🚫 No refresh token or remember me: force logout
      if (!rememberMe || !refreshToken) {
        setSession(null);
        resetUser();
        clearTokens();
        window.location.href = "/auth/jwt/login";
      }

      originalRequest._retry = true;

      // 🛑 Already refreshing: queue this request
      if (getIsRefreshing()) {
        return new Promise((resolve, reject) => {
          addToQueue({ resolve, reject });
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return axiosInstance(originalRequest);
        });
      }

      // ✅ Set refreshing flag immediately to block duplicates
      setIsRefreshing(true);

      try {
        const res = await refreshTokenRequest();
        const newAccessToken = res.data?.access;

        if (!newAccessToken) throw new Error("No access token returned");

        // 🪪 Save new token in storage + axios headers
        // Workspace header is managed by WorkspaceProvider (reads from sessionStorage).
        // Organization ID is read from the original request headers object.
        const organizationId =
          originalRequest?.headers?.["X-Organization-Id"] || null;
        setSession(newAccessToken, organizationId);

        // 🔄 Re-apply per-tab headers from sessionStorage (survives refresh)
        const wsId = sessionStorage.getItem("workspaceId");
        if (wsId) {
          axiosInstance.defaults.headers.common["X-Workspace-Id"] = wsId;
        }
        const orgId = sessionStorage.getItem("organizationId");
        if (orgId) {
          axiosInstance.defaults.headers.common["X-Organization-Id"] = orgId;
        }

        // 🧠 Process queued requests
        processQueue(null, newAccessToken);

        // 🔁 Retry original failed request with new token
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return axiosInstance(originalRequest);
      } catch (err) {
        processQueue(err, null); // ❌ Fail queued requests too
        setSession(null);
        resetUser();
        clearTokens();

        // Check if the user was deactivated and pass message to login page
        const errorMessage = err?.response?.data?.detail || err?.message || "";
        const isDeactivated =
          errorMessage.toLowerCase().includes("deactivated") ||
          errorMessage.toLowerCase().includes("inactive");
        if (isDeactivated) {
          sessionStorage.setItem(
            "auth_error",
            "Your account has been deactivated. Please contact your administrator.",
          );
        }

        window.location.href = "/auth/jwt/login";
        return Promise.reject(err);
      } finally {
        setIsRefreshing(false); // 🧼 Always reset
      }
    }

    // Only logout for authentication-related errors
    // No need to check for 401 here as the above refresh block handles it
    // a 403 (forbidden) error specifically from authentication endpoints

    const isAuthError =
      status === RESPONSE_CODES.FORBIDDEN &&
      authEndpoints.some((endpoint) => url?.includes(endpoint));

    // Log the error for debugging (but don't log out for non-auth errors)
    if (
      status >= RESPONSE_CODES.BAD_REQUEST &&
      status !== RESPONSE_CODES.UNAUTHORIZED
    ) {
      logger.debug("API Error:", {
        status: status,
        url: url,
        isAuthError,
        willLogout: isAuthError && !avoid,
        avoidReason: avoid ? "On auth page" : null,
      });
    }

    if (isAuthError && !avoid) {
      logger.warn("Authentication error detected, logging out user");
      setSession(null);
      resetUser();
      clearTokens();
      window.location.href = "/auth/jwt/login";
    }

    const errData = (error.response && error.response.data) || {
      message: "Something went wrong",
    };

    const customError = {
      ...errData,
      statusCode: error.response?.status,
    };

    try {
      addCamelAliases(customError, new WeakSet());
    } catch {
      // Preserve the original API error.
    }

    return Promise.reject(customError);
  },
);

export default axiosInstance;

// ----------------------------------------------------------------------

export const fetcher = async (args) => {
  const [url, config] = Array.isArray(args) ? args : [args];

  const res = await axiosInstance.get(url, { ...config });

  return res.data;
};

export const fetchWithPost = async (args) => {
  const [url, config] = Array.isArray(args) ? args : [args];

  const res = await axiosInstance.post(url, { ...config });

  return res.data;
  // return response.json();
};

// ----------------------------------------------------------------------

export const endpoints = {
  getStarted: {
    getTabs: apiPath("/accounts/first-checks/"),
  },
  overview: {
    dashboardSummary: apiPath("/model-hub/overview/"),
  },
  keys: {
    keys: apiPath("/accounts/keys/"),
    getKeys: apiPath("/accounts/key/get_secret_keys/"),
    enableKey: apiPath("/accounts/key/enable_key/"),
    disablekey: apiPath("/accounts/key/disable_key/"),
    deleteKey: apiPath("/accounts/key/delete_secret_key/"),
    generateSecretKey: apiPath("/accounts/key/generate_secret_key/"),
  },
  auth: {
    me: apiPath("/accounts/user-info/"),
    login: apiPath("/accounts/token/"),
    register: apiPath("/accounts/signup/"),
    user_onboarding_info: apiPath("/accounts/onboarding/"),
    passwordResetInitiate: apiPath("/accounts/password-reset-initiate/"),
    passwordReset: (uidb64, token) =>
      apiPath("/accounts/password-reset-confirm/{uidb64}/{token}/", {
        uidb64,
        token,
      }),
    service: (provider) => `/saml2_auth/login/?provider=${provider}`,
    create_org: apiPath("/accounts/team/users/"),
    ssoLogin: (email) => `/saml2_auth/idp-login/?email=${email}`,
    logout: apiPath("/accounts/logout/"),
    refreshToken: apiPath("/accounts/token/refresh/"),
    awsSignUp: apiPath("/accounts/aws-marketplace/signup/"),
    config: apiPath("/accounts/config/"),
    createOrganization: apiPath("/accounts/organizations/create/"),
  },
  workspace: {
    getMembers: (workspace_id) =>
      `/accounts/workspaces/${workspace_id}/members/`,
    userList: apiPath("/accounts/user/list/"),
    workspaceList: apiPath("/accounts/workspace/list/"),
    updateRole: apiPath("/accounts/user/role/update/"),
    resendInvite: apiPath("/accounts/user/resend-invite/"),
    deleteUser: apiPath("/accounts/user/delete/"),
    workspaceInvite: apiPath("/accounts/workspace/invite/"),
    deactivate: apiPath("/accounts/user/deactivate/"),
    removeUserFromWrokspace: (workspace_id, member_id) =>
      `/accounts/workspaces/${workspace_id}/members/${member_id}/`,
    workspaceUpdate: (workspace_id) => `/accounts/workspaces/${workspace_id}/`,
  },
  // New RBAC endpoints (Phase 2)
  rbac: {
    inviteCreate: apiPath("/accounts/organization/invite/"),
    inviteResend: apiPath("/accounts/organization/invite/resend/"),
    inviteCancel: apiPath("/accounts/organization/invite/cancel/"),
    memberList: apiPath("/accounts/organization/members/"),
    memberRoleUpdate: apiPath("/accounts/organization/members/role/"),
    memberRemove: apiPath("/accounts/organization/members/remove/"),
    memberReactivate: apiPath("/accounts/organization/members/reactivate/"),
    workspaceMemberList: (wsId) => `/accounts/workspace/${wsId}/members/`,
    workspaceMemberRoleUpdate: (wsId) =>
      `/accounts/workspace/${wsId}/members/role/`,
    workspaceMemberRemove: (wsId) =>
      `/accounts/workspace/${wsId}/members/remove/`,
  },
  invite: {
    accept_invitation: (uidb64, token) =>
      apiPath("/accounts/accept-invitation/{uidb64}/{token}/", {
        uidb64,
        token,
      }),
  },
  model: {
    list: legacyApiPath(
      "/model-hub/ai-models/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    details: legacyApiPath(
      "/model-hub/ai-models/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    updateMetric: legacyApiPath(
      "/model-hub/ai-models/update-metric/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    performance: legacyApiPath(
      "/model-hub/ai-models/performance",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    create: legacyApiPath(
      "/model-hub/ai_models/create/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    updateDefaultDataset: legacyApiPath(
      "/model-hub/ai_models/update-baseline/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    modelList: legacyApiPath(
      "/model-hub/ai-models/list/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
    deleteModel: (id) =>
      legacyApiPath(
        "/model-hub/ai_models/delete/{id}/",
        { id },
        "Legacy model-management API is not exposed in Swagger yet.",
      ),
    getModelDetail: legacyApiPath(
      "/model-hub/get-model-details/",
      "Legacy model-management API is not exposed in Swagger yet.",
    ),
  },
  dataset: {
    list: legacyApiPath(
      "/model-hub/dataset/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    summary: legacyApiPath(
      "/model-hub/dataset/summary",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    options: legacyApiPath(
      "/model-hub/dataset/options/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    getColumns: legacyApiPath(
      "/model-hub/dataset/column-config/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    updateColumns: legacyApiPath(
      "/model-hub/dataset/column-config/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    createDataset: legacyApiPath(
      "/model-hub/dataset/create/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    propertyList: legacyApiPath(
      "/model-hub/dataset/properties/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    propertyDetail: (id) =>
      legacyApiPath(
        "/model-hub/dataset/properties/{id}/",
        { id },
        "Legacy model dataset API is not exposed in Swagger yet.",
      ),
    createProperty: legacyApiPath(
      "/model-hub/dataset/properties/",
      "Legacy model dataset API is not exposed in Swagger yet.",
    ),
    promptSummary: (id) => `/model-hub/dataset/${id}/run-prompt-stats/`,
    evalsSummary: (id) => `/model-hub/dataset/${id}/eval-stats/`,
    annotationSummary: (id) =>
      apiPath("/model-hub/dataset/{dataset_id}/annotation-summary/", {
        dataset_id: id,
      }),
    baseColumndata: apiPath("/model-hub/datasets/get-base-columns/"),
    criticalIssue: (id) => `/model-hub/datasets/explanation-summary/${id}/`,
    criticalIssueRefresh: (id) =>
      `/model-hub/datasets/explanation-summary/${id}/refresh/`,
    getCompareDataset: (id) => `/model-hub/datasets/${id}/compare-datasets/`,
    getCompareDatasetDownload: (id) =>
      `/model-hub/datasets/${id}/compare-datasets/download/`,
    getSummaryTable: (id) => `/model-hub/datasets/${id}/compare-stats/`,
    getCompareDatasetRow: (compareId, rowId) =>
      `/model-hub/datasets/get-compare-row/${compareId}/${rowId}/`,
    deleteCompareDataset: (compareId) =>
      `/model-hub/datasets/delete-compare/${compareId}/`,
  },
  dataPoints: {
    getColumns: legacyApiPath(
      "/model-hub/data-points/column-config/",
      "Legacy model data-points API is not exposed in Swagger yet.",
    ),
    updateColumns: legacyApiPath(
      "/model-hub/data-points/column-config/",
      "Legacy model data-points API is not exposed in Swagger yet.",
    ),
    list: legacyApiPath(
      "/model-hub/data-points/",
      "Legacy model data-points API is not exposed in Swagger yet.",
    ),
    create: legacyApiPath(
      "/model-hub/data-points/create/",
      "Legacy model data-points API is not exposed in Swagger yet.",
    ),
    metrics: legacyApiPath(
      "/model-hub/data-points/metrics/",
      "Legacy model data-points API is not exposed in Swagger yet.",
    ),
  },
  event: {
    names: legacyApiPath(
      "/model-hub/event-names/",
      "Legacy model event API is not exposed in Swagger yet.",
    ),
    list: legacyApiPath(
      "/model-hub/events/",
      "Legacy model event API is not exposed in Swagger yet.",
    ),
    uniqueProperties: legacyApiPath(
      "/model-hub/unique-properties/",
      "Legacy model event API is not exposed in Swagger yet.",
    ),
  },
  annotation: {
    list: apiPath("/model-hub/annotation-tasks/"),
    annotationLabelText: apiPath("/model-hub/annotations-labels/"),
    annotationsListByDataSetId: (dataSetId) =>
      `${apiPath("/model-hub/annotations/")}?dataset=${dataSetId}`,
    previewAnnotations: apiPath("/model-hub/annotations/preview_annotations/"),
    createNewAnnotation: apiPath("/model-hub/annotations/"),
    getAnnotationById: (id) => apiPath("/model-hub/annotations/{id}/", { id }),
    putAnnotationById: apiPath("/model-hub/annotations/"),
    annotateRow: (id) =>
      apiPath("/model-hub/annotations/{id}/annotate_row/", { id }),
    annotationsUser: (id) => `/model-hub/organizations/${id}/users/`,
    deleteAnnotation: (id) => apiPath("/model-hub/annotations/{id}/", { id }),
    deleteAnnotations: apiPath("/model-hub/annotations/bulk_destroy/"),
    updateAnnotation: (annotationId) =>
      apiPath("/model-hub/annotations/{id}/update_cells/", {
        id: annotationId,
      }),
    resetAnnotation: (annotationId) =>
      apiPath("/model-hub/annotations/{id}/reset_annotations/", {
        id: annotationId,
      }),
  },
  knowledge: {
    knowledgeBase: apiPath("/model-hub/knowledge-base/"),
    list: apiPath("/model-hub/knowledge-base/get/"),
    files: apiPath("/model-hub/knowledge-base/files/"),
  },
  customMetric: {
    list: legacyApiPath(
      "/model-hub/custom-metric/",
      "Legacy custom metric collection API is not exposed in Swagger yet.",
    ),
    create: apiPath("/model-hub/custom-metric/create/"),
    edit: apiPath("/model-hub/custom-metric/update/"),
    all: "/model-hub/custom-metric/all/",
    tagOptions: "/model-hub/custom-metric/tag-options/",
    testMetric: apiPath("/model-hub/custom-metric/test/"),
  },
  performance: {
    graphData: legacyApiPath(
      "/model-hub/performance/",
      "Legacy model performance collection API is not exposed in Swagger yet.",
    ),
    tableData: "/model-hub/performance/detail/",
    tableExport: "/model-hub/performance/export/",
    getFilterOptions: (modelId) => `/model-hub/performance/options/${modelId}/`,
    getTagDistribution: (modelId) =>
      `/model-hub/performance/tag-distribution/${modelId}/`,
  },
  performanceReport: {
    create: (modelId) => `/model-hub/performance/report/${modelId}/`,
    list: (modelId) => `/model-hub/performance/report/${modelId}/`,
    delete: (modelId, reportId) =>
      `/model-hub/performance/report/${modelId}/${reportId}/`,
  },
  connectors: {
    getDraftId: "/data-connector/draft/",
    getDraftData: "/data-connector/draft/",
    testConnection: "/data-connector/test/",
    updateDraft: "/data-connector/draft/",
  },
  connections: {
    getConnectionCount: "/data-connector/connection-count/",
    createConnection: "/data-connector/connection/",
    getConnectionList: "/data-connector/connection/",
    getConnectionJobs: "/data-connector/jobs/",
    deleteConnection: "/data-connector/connection/",
    updateConnection: "/data-connector/connection/",
  },
  optimization: {
    createOptimization: apiPath("/model-hub/optimize-dataset/"),
    stopOptimization: (id) => `/model-hub/dataset-optimization/${id}/stop/`,
    getAll: apiPath("/model-hub/optimize-dataset/"),
    getColumns: (id) => `/model-hub/optimize-dataset/${id}/column-config/`,
    updateColumns: (id) => `/model-hub/optimize-dataset/${id}/column-config/`,
    getOptimizeRightAnswer: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/right-answers/${optimization_id}/`,
    getRightAnsColumns: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/column-config/right-answers/${optimization_id}/`,
    updateRightAnsColumns: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/column-config/right-answers/${optimization_id}/`,
    getPromptTemplateExplore: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/prompt-template-explore/${optimization_id}/`,
    getPromptTemplateExploreColumns: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/column-config/prompt-template-explore/${optimization_id}/`,
    updatePromptTemplateExploreColumns: (model_id, optimization_id) =>
      `/model-hub/optimize-dataset/${model_id}/column-config/prompt-template-explore/${optimization_id}/`,
    getPromptTemplateResults: (modelId, optimizationId) =>
      `/model-hub/optimize-dataset/${modelId}/prompt-template-result/${optimizationId}/`,
    getOptimizationDetail: (modelId, optimizationId) =>
      `/model-hub/optimize-dataset/${modelId}/${optimizationId}/`,
  },
  settings: {
    teams: {
      getMemberList: apiPath("/accounts/team/users/"),
      deleteMember: (id) => `/accounts/team/users/${id}/`,
      inviteMember: apiPath("/accounts/team/users/"),
    },
    apiKeys: apiPath("/model-hub/api-keys/"),
    customModal: {
      getCustomModal: apiPath("/model-hub/custom-models/"),
      createCustomModal: apiPath("/model-hub/custom_models/create/"),
      editCustomModel: apiPath("/model-hub/custom_models/edit/"),
      deleteModel: apiPath("/model-hub/custom_models/delete/"),
    },
    getLatestPrices: apiPath("/usage/get_latest_prices/"),
    getAvailableMonths: legacyApiPath(
      "/usage/available-months/",
      "Legacy/EE usage billing API is not exposed in Swagger yet.",
    ),
    usageTotals: apiPath("/usage/workspace-usage-summary/"),
    workspaceUsage: apiPath("/usage/workspace-eval-summary/"),
    usageMetrics: apiPath("/usage/usage-summary/"),
    v2: {
      usageOverview: legacyApiPath(
        "/usage/v2/usage-overview/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      usageTimeSeries: legacyApiPath(
        "/usage/v2/usage-time-series/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      usageWorkspaceBreakdown: legacyApiPath(
        "/usage/v2/usage-workspace-breakdown/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      plansAndAddons: legacyApiPath(
        "/usage/v2/plans-and-addons/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      billingOverview: legacyApiPath(
        "/usage/v2/billing-overview/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      invoices: legacyApiPath(
        "/usage/v2/invoices/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      invoiceDetail: (id) =>
        legacyApiPath(
          "/usage/v2/invoices/{id}/",
          { id },
          "Legacy/EE usage billing API is not exposed in Swagger yet.",
        ),
      notifications: legacyApiPath(
        "/usage/v2/notifications/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      budgets: legacyApiPath(
        "/usage/v2/budgets/",
        "Legacy/EE usage billing API is not exposed in Swagger yet.",
      ),
      budgetDetail: (id) =>
        legacyApiPath(
          "/usage/v2/budgets/{id}/",
          { id },
          "Legacy/EE usage billing API is not exposed in Swagger yet.",
        ),
      upgradeToPayg: apiPath("/usage/v2/upgrade-to-payg/"),
      downgradeToFree: apiPath("/usage/v2/downgrade-to-free/"),
      addAddon: apiPath("/usage/v2/add-addon/"),
      removeAddon: apiPath("/usage/v2/remove-addon/"),
      reinstateAddon: apiPath("/usage/v2/reinstate-addon/"),
      paymentMethods: apiPath("/usage/v2/payment-methods/"),
      paymentMethodSetupIntent: apiPath(
        "/usage/v2/payment-methods/setup-intent/",
      ),
      paymentMethodDefault: (pmId) =>
        `/usage/v2/payment-methods/${pmId}/default/`,
      paymentMethodDelete: (pmId) => `/usage/v2/payment-methods/${pmId}/`,
      deploymentInfo: apiPath("/api/deployment-info/"),
    },
  },
  tools: {
    create: apiPath("/model-hub/tools/"),
    update: (id) => `/model-hub/tools/${id}/`,
  },
  secrets: {
    list: apiPath("/model-hub/secrets/"),
    create: apiPath("/model-hub/secrets/"),
  },
  huggingFace: {
    list: apiPath("/model-hub/datasets/huggingface/list/"),
    detail: apiPath("/model-hub/datasets/huggingface/detail/"),
    addHuggingFaceRow: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_rows_from_huggingface/`,
  },
  develop: {
    modelList: apiPath("/model-hub/api/models_list/"),
    modelParams: apiPath("/model-hub/api/model_parameters/"),
    getDatasets: () => apiPath("/model-hub/develops/get-datasets/"),
    getDerivedDatasets: (datasetId) =>
      apiPath("/model-hub/develops/get-derived-datasets/{dataset_id}/", {
        dataset_id: datasetId,
      }),
    getDatasetList: () => apiPath("/model-hub/develops/get-datasets-names/"),
    getCellData: apiPath("/model-hub/develops/get-cell-data/"),
    getRowsDiff: apiPath("/model-hub/experiments/v2/row-diff/"),
    getDatasetColumns: (datasetId) =>
      `/model-hub/dataset/columns/${datasetId}/`,
    getJsonColumnSchema: (datasetId) =>
      `/model-hub/dataset/${datasetId}/json-schema/`,
    getDatasetDetail: (datasetId) =>
      `/model-hub/develops/${datasetId}/get-dataset-table/`,
    updateCellValue: (datasetId) =>
      `/model-hub/develops/${datasetId}/update_cell_value/`,
    downloadDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/download_dataset/`,
    updateDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/edit_dataset_behavior/`,
    uploadDatasetLocalFile: apiPath(
      "/model-hub/develops/create-dataset-from-local-file/",
    ),
    uploadDatasetRow: apiPath("/model-hub/develops/add_rows_from_file/"),
    addEmptyRow: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_empty_rows/`,
    getSyntheticConfig: (datasetId) =>
      `/model-hub/develops/${datasetId}/synthetic-config/`,
    createSyntheticDataset: apiPath(
      "/model-hub/develops/create-synthetic-dataset/",
    ),
    updateSyntheticDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/update-synthetic-config/`,
    addSyntheticDataset: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_synthetic_data/", {
        dataset_id: datasetId,
      }),
    createDatasetManually: apiPath(
      "/model-hub/develops/create-dataset-manually/",
    ),
    createEmptyDataset: apiPath("/model-hub/develops/create-empty-dataset/"),
    getHuggingFaceDataset: apiPath(
      "/model-hub/develops/get-huggingface-dataset-config/",
    ),
    createHuggingFaceDataset: apiPath(
      "/model-hub/develops/create-dataset-from-huggingface/",
    ),
    cloneDataset: (newDatasetId) =>
      `/model-hub/develops/clone-dataset/${newDatasetId}/`,
    createFromExistingDataset: apiPath("/model-hub/develops/add-as-new/"),
    addAsNewDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/create-dataset/`,
    individualExperimentDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/get-experiment-dataset-table/`,
    addColumn: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_static_column/`,
    addMultipleColumns: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_multiple_static_columns/`,
    updateColumnName: (datasetId, columnId) =>
      `/model-hub/develops/${datasetId}/update_column_name/${columnId}/`,
    updateColumnType: (datasetId, columnId) =>
      `/model-hub/develops/${datasetId}/update_column_type/${columnId}/`,
    deleteColumn: (datasetId, columnId) =>
      `/model-hub/develops/${datasetId}/delete_column/${columnId}/`,
    deleteDataset: () => apiPath("/model-hub/develops/delete_dataset/"),
    addDatasetColumn: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_columns/`,
    addRowFromExistingDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_rows_from_existing_dataset/`,
    getRowData: (datasetId) => `/model-hub/develops/${datasetId}/get-row-data/`,
    addColumns: {
      apiCall: (datasetId) =>
        `/model-hub/datasets/${datasetId}/add-api-column/`,
      executeCode: (datasetId) =>
        legacyApiPath(
          "/model-hub/datasets/{dataset_id}/execute-code/",
          { dataset_id: datasetId },
          "Deprecated dynamic column execute-code route is no longer present in backend URLs.",
        ),
      extractEntities: (datasetId) =>
        `/model-hub/datasets/${datasetId}/extract-entities/`,
      classifyColumn: (datasetId) =>
        `/model-hub/datasets/${datasetId}/classify-column/`,
      extractJsonKey: (datasetId) =>
        `/model-hub/develops/${datasetId}/extract-json-column/`,
      addVectorDBColumn: (datasetId) =>
        `/model-hub/datasets/${datasetId}/add_vector_db_column/`,
      preview: (datasetId, operationType) =>
        `/model-hub/datasets/${datasetId}/preview/${operationType}/`,
      conditionalnode: (datasetId) =>
        `/model-hub/datasets/${datasetId}/conditional-column/`,
      getColumnConfig: (columnId) =>
        `/model-hub/columns/${columnId}/operation-config/`,
      updateDynamicColumn: (columnId) =>
        `/model-hub/columns/${columnId}/rerun-operation/`,
    },
    deleteDatasetRow: (datasetId) =>
      `/model-hub/develops/${datasetId}/delete_row/`,
    duplicateDatasetRows: (datasetId) =>
      `/model-hub/datasets/${datasetId}/duplicate-rows/`,
    createDatasetRows: (datasetId) =>
      `/model-hub/datasets/${datasetId}/duplicate/`,
    mergeDatasetRows: (datasetId) => `/model-hub/datasets/${datasetId}/merge/`,
    evaluateRows: () => apiPath("/model-hub/evaluate-rows/"),
    evaluateRunRows: () => apiPath("/model-hub/run-prompt-for-rows/"),
    eval: {
      createCustomEval: apiPath("/model-hub/create_custom_evals/"),
      getEvalsList: (datasetId) =>
        `/model-hub/develops/${datasetId}/get_evals_list/`,
      getCompareEvalsList: () =>
        apiPath("/model-hub/datasets/compare/get-evals-list/"),
      getEvalTemplateConfig: (templateId) =>
        legacyApiPath(
          "/model-hub/develops/get_preset_eval_structure/{template_id}/",
          { template_id: templateId },
          "Legacy preset eval structure API is not present in backend URLs.",
        ),
      getPreviouslyConfiguredEvalTemplateConfig: (datasetId, templateId) =>
        `/model-hub/develops/${datasetId}/get_eval_structure/${templateId}/`,
      addEval: (datasetId) => `/model-hub/develops/${datasetId}/add_user_eval/`,
      addCompareEval: (datasetId) =>
        `/model-hub/datasets/${datasetId}/compare-datasets/add-eval/`,
      runEvals: (datasetId) =>
        `/model-hub/develops/${datasetId}/start_evals_process/`,
      compareRunEvals: (datasetId) =>
        `/model-hub/datasets/${datasetId}/compare-datasets/start-eval/`,
      deleteEval: (datasetId, evalId) =>
        `/model-hub/develops/${datasetId}/delete_user_eval/${evalId}/`,
      editEval: (datasetId, evalId) =>
        `/model-hub/develops/${datasetId}/edit_and_run_user_eval/${evalId}/`,
      stopEval: (datasetId, evalId) =>
        `/model-hub/develops/${datasetId}/stop_user_eval/${evalId}/`,
      testEval: (datasetId) =>
        `/model-hub/develops/${datasetId}/preview_run_eval/`,
      getFunctionEvalsList: apiPath("/model-hub/develops/get_function_list/"),
      addFeedback: apiPath("/model-hub/feedback/"),
      getFeedbackTemplate: apiPath("/model-hub/feedback/get_template/"),
      getFeedbackTemplateTrace: (id) =>
        apiPath("/tracer/custom-eval-config/{id}/", { id }),
      updateFeedback: apiPath("/model-hub/feedback/submit-feedback/"),
      getFeedbackDetails: apiPath("/model-hub/feedback/get-feedback-details/"),
      getEvalLogs: apiPath("/model-hub/get-eval-logs"),
      runCellErrorLocalizer: (cellId) =>
        `/model-hub/cells/${cellId}/run-error-localizer/`,
      getCellErrorLocalizer: (cellId) =>
        `/model-hub/cells/${cellId}/run-error-localizer/`,
      getEvalsLogs: apiPath("/model-hub/get-eval-logs-details"),
      getEvalMetrics: apiPath("/model-hub/get-eval-metrics"),
      getEvalFeedbacks: legacyApiPath(
        "/model-hub/get-eval-feedback",
        "Legacy eval feedback API is not present in backend URLs.",
      ),
      getEvalTemplates: apiPath("/model-hub/get-eval-templates"),
      listEvalTemplates: apiPath("/model-hub/eval-templates/list/"),
      listEvalTemplateCharts: apiPath("/model-hub/eval-templates/list-charts/"),
      bulkDeleteEvalTemplates: apiPath(
        "/model-hub/eval-templates/bulk-delete/",
      ),
      createEvalTemplateV2: apiPath("/model-hub/eval-templates/create-v2/"),
      createCompositeEval: apiPath(
        "/model-hub/eval-templates/create-composite/",
      ),
      getEvalVersions: (id) => `/model-hub/eval-templates/${id}/versions/`,
      createEvalVersion: (id) =>
        `/model-hub/eval-templates/${id}/versions/create/`,
      setDefaultVersion: (templateId, versionId) =>
        `/model-hub/eval-templates/${templateId}/versions/${versionId}/set-default/`,
      restoreVersion: (templateId, versionId) =>
        `/model-hub/eval-templates/${templateId}/versions/${versionId}/restore/`,
      getCompositeDetail: (id) => `/model-hub/eval-templates/${id}/composite/`,
      executeCompositeEval: (id) =>
        `/model-hub/eval-templates/${id}/composite/execute/`,
      executeCompositeEvalAdhoc: apiPath(
        "/model-hub/eval-templates/composite/execute-adhoc/",
      ),
      getEvalDetail: (id) => `/model-hub/eval-templates/${id}/detail/`,
      updateEvalTemplate: (id) => `/model-hub/eval-templates/${id}/update/`,
      getEvalUsage: (id) => `/model-hub/eval-templates/${id}/usage/`,
      getEvalFeedbackList: (id) =>
        `/model-hub/eval-templates/${id}/feedback-list/`,
      // Ground Truth (Phase 9)
      getGroundTruthList: (id) =>
        `/model-hub/eval-templates/${id}/ground-truth/`,
      uploadGroundTruth: (id) =>
        `/model-hub/eval-templates/${id}/ground-truth/upload/`,
      getGroundTruthConfig: (id) =>
        `/model-hub/eval-templates/${id}/ground-truth-config/`,
      updateGroundTruthConfig: (id) =>
        `/model-hub/eval-templates/${id}/ground-truth-config/`,
      groundTruthMapping: (id) => `/model-hub/ground-truth/${id}/mapping/`,
      groundTruthRoleMapping: (id) =>
        `/model-hub/ground-truth/${id}/role-mapping/`,
      groundTruthData: (id) => `/model-hub/ground-truth/${id}/data/`,
      groundTruthStatus: (id) => `/model-hub/ground-truth/${id}/status/`,
      groundTruthSearch: (id) => `/model-hub/ground-truth/${id}/search/`,
      groundTruthEmbed: (id) => `/model-hub/ground-truth/${id}/embed/`,
      deleteGroundTruth: (id) => `/model-hub/ground-truth/${id}/`,
      runEval: legacyApiPath(
        "/model-hub/run-eval",
        "Legacy standalone run-eval API is not present in backend URLs.",
      ),
      getEvalConfigs: apiPath("/model-hub/get-eval-config"),
      getEvalNames: apiPath("/model-hub/get-eval-template-names"),
      aiFilter: apiPath("/model-hub/ai-filter/"),
      aiEvalWriter: apiPath("/model-hub/ai-eval-writer/"),
      summaryTemplates: apiPath("/model-hub/eval-summary-templates/"),
      summaryTemplate: (id) => `/model-hub/eval-summary-templates/${id}/`,
      evalPlayground: apiPath("/model-hub/eval-playground/"),
      updateEvalsTemplate: apiPath("/model-hub/update-eval-template/"),
      testEvaluation: apiPath("/model-hub/test-evaluation/"),
      evalPlaygroundLog: legacyApiPath(
        "/model-hub/eval-playground-logs/",
        "Legacy eval playground logs API is not present in backend URLs.",
      ),
      addEvalsFeedback: apiPath("/model-hub/eval-playground/feedback/"),
      duplicateEvalsTemplate: apiPath("/model-hub/duplicate-eval-template/"),
      deleteEvalsTemplate: apiPath("/model-hub/delete-eval-template/"),
      evalsSDKCode: apiPath("/model-hub/eval-sdk-code/"),
      groupEvals: apiPath("/model-hub/eval-groups/"),
      editGroupEvalList: apiPath("/model-hub/eval-groups/edit-eval-list/"),
      applyEvalGroup: apiPath("/model-hub/eval-groups/apply-eval-group/"),
    },
    runPrompt: {
      create: apiPath("/model-hub/develops/add_run_prompt_column/"),
      preview: apiPath("/model-hub/develops/preview_run_prompt_column/"),
      runPromptOptions: apiPath(
        "/model-hub/develops/retrieve_run_prompt_options/",
      ),
      voiceOptions: apiPath("/model-hub/api/model_voices/"),
      createCustomVoice: apiPath("/model-hub/tts-voices/"),
      createTemplateId: apiPath("/model-hub/prompt-templates/"),
      createPromptDraft: apiPath("/model-hub/prompt-templates/create-draft/"),
      getPrompt: (id) => `/model-hub/prompt-templates/${id}/`,
      getNameChange: (id) => `/model-hub/prompt-templates/${id}/save-name/`,
      generatePrompt: apiPath("/model-hub/prompt-templates/generate-prompt/"),
      generateVariables: apiPath(
        "/model-hub/prompt-templates/generate-variables/",
      ),
      getStatus: (/** @type {string} */ id) =>
        `/model-hub/prompt-templates/${id}/get-run-status/`,
      getPromptVersions: () => apiPath("/model-hub/prompt-history-executions/"),
      // https://dev.api.futureagi.com/model-hub/prompt-templates/6b6b4d0b-ef4f-4a8b-82e9-2d2bbaedc6b5/run_template/
      runTemplatePrompt: (id) =>
        `/model-hub/prompt-templates/${id}/run_template/`,
      getRunPrompt: () =>
        apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
      editRunPrompt: () =>
        apiPath("/model-hub/develops/edit_run_prompt_column/"),
      applyVariables: () => apiPath("/model-hub/get-column-values/"),
      promptExecutions: () => apiPath("/model-hub/prompt-executions/"),
      promptDelete: (id) => `/model-hub/prompt-templates/${id}/`,
      promptMultiDelete: apiPath("/model-hub/prompt-templates/bulk-delete/"),
      analyzePrompt: apiPath("/model-hub/prompt-templates/analyze-prompt/"),
      improvePrompt: apiPath("/model-hub/prompt-templates/improve-prompt/"),
      updatePrompt: apiPath("/model-hub/prompt-templates/improve-prompt/"),
      responseSchema: apiPath("/model-hub/response_schema/"),
      saveDefaultPrompt: (id) =>
        `/model-hub/prompt-templates/${id}/set_default/`,
      commitSavePrompt: (id) => `/model-hub/prompt-templates/${id}/commit/`,
      getAllVariables: (id) =>
        `/model-hub/prompt-templates/${id}/all-variables/`,
      getDerivedVariables: (id) =>
        `/model-hub/prompt-templates/${id}/derived-variables/`,
      getDerivedVariableSchema: (id, columnName) =>
        `/model-hub/prompt-templates/${id}/derived-variables/${columnName}/schema/`,
      extractDerivedVariables: (id) =>
        `/model-hub/prompt-templates/${id}/derived-variables/extract/`,
      previewDerivedVariables: apiPath(
        "/model-hub/prompt-templates/derived-variables/preview/",
      ),
      getDatasetDerivedVariables: (datasetId) =>
        `/model-hub/datasets/${datasetId}/derived-variables/`,
      compareVersions: (id) =>
        `/model-hub/prompt-templates/${id}/compare-versions/`,
      addDraftInPrompt: (id) =>
        `/model-hub/prompt-templates/${id}/add-new-draft/`,
      stopGenerating: (id) =>
        `/model-hub/prompt-templates/${id}/stop-streaming/`,
      getEvaluationData: (id) =>
        `/model-hub/prompt-templates/${id}/evaluations/`,
      getEvaluationConfigs: (id) =>
        `/model-hub/prompt-templates/${id}/evaluation-configs/`,
      createOrUpdateEvalConfig: (id) =>
        `/model-hub/prompt-templates/${id}/update-evaluation-configs/`,
      deleteEvalConfig: (promptTemplate, evalId) =>
        `/model-hub/prompt-templates/${promptTemplate}/delete-evaluation-config/?id=${evalId}`,
      runEvalsOnMultipleVersions: (id) =>
        `/model-hub/prompt-templates/${id}/run-evals-on-multiple-versions/`,
      promptLabels: apiPath("/model-hub/prompt-labels/"),
      createPromptLabel: apiPath("/model-hub/prompt-labels/"),
      deletePromptLabel: (id) => `/model-hub/prompt-labels/${id}/`,
      assignLabels: (promptId, labelId) =>
        `/model-hub/prompt-labels/${promptId}/${labelId}/assign-label-by-id/`,
      assignMultipleLabels: apiPath(
        "/model-hub/prompt-labels/assign-multiple-labels/",
      ),
      removeLabel: () => apiPath("/model-hub/prompt-labels/remove/"),
      getPromptMetrics: () => apiPath("/model-hub/prompt/metrics/"),
      getPromptSpanMetrics: () => apiPath("/model-hub/prompt/span-metrics/"),
      promptMetricEmptyScreen: () =>
        apiPath("/model-hub/prompt/metrics/empty-screen"),
      promptFolder: apiPath("/model-hub/prompt-folders/"),
      promptFolderId: (id) => `/model-hub/prompt-folders/${id}/`,
      movePrompt: (folderId) =>
        `/model-hub/prompt-templates/${folderId}/save-prompt-folder/`,
      promptTemplate: apiPath("/model-hub/prompt-base-templates/"),
      promptTemplateId: (id) => `/model-hub/prompt-base-templates/${id}/`,
      categories: apiPath(
        "/model-hub/prompt-base-templates/get-all-categories/",
      ),
    },
    optimizeDevelop: {
      columnInfo: apiPath("/model-hub/metrics/by-column/"),
      create: apiPath("/model-hub/optimisation/create/"),
      list: apiPath("/model-hub/optimisation/"),
      detail: (optimizationId) =>
        `/model-hub/optimisation/${optimizationId}/details/`,
    },
    datasetOptimization: {
      create: apiPath("/model-hub/dataset-optimization/"),
      list: apiPath("/model-hub/dataset-optimization/"),
      detail: (id) => `/model-hub/dataset-optimization/${id}/`,
      steps: (id) => `/model-hub/dataset-optimization/${id}/steps/`,
      graph: (id) => `/model-hub/dataset-optimization/${id}/graph/`,
      trialPrompt: (id, trialId) =>
        `/model-hub/dataset-optimization/${id}/trial/${trialId}/prompt/`,
      trialDetail: (id, trialId) =>
        `/model-hub/dataset-optimization/${id}/trial/${trialId}/`,
      trialScenarios: (id, trialId) =>
        `/model-hub/dataset-optimization/${id}/trial/${trialId}/scenarios/`,
      trialEvaluations: (id, trialId) =>
        `/model-hub/dataset-optimization/${id}/trial/${trialId}/evaluations/`,
    },
    experiment: {
      index: apiPath("/model-hub/experiments/v2/"),
      update: (id) => `/model-hub/experiments/v2/${id}/`,
      create: () => apiPath("/model-hub/experiments/v2/"),
      getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
      experimentListPaginated: apiPath("/model-hub/experiments/v2/list/"),
      experimentList: apiPath("/model-hub/experiment-detail/"),
      experimentDetail: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/rows/`,
      downloadExperiment: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/download/`,
      runEvaluation: (experimentId) =>
        `/model-hub/experiments/${experimentId}/run-evaluations/`,
      addEval: (experimentId) =>
        `/model-hub/experiments/${experimentId}/add-eval/`,
      getSummary: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/stats/`,
      compareExperiments: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/compare-experiments/`,
      comparison: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/comparisons/`,
      // deleteExperiment: () => `/model-hub/experiments/delete/`,
      rerun: apiPath("/model-hub/experiments/v2/re-run/"),
      delete: apiPath("/model-hub/experiments/v2/delete/"),
      stop: (id) => `/model-hub/experiments/v2/${id}/stop/`,
      rowDetail: (experimentId, rowId) =>
        `/model-hub/experiments/${experimentId}/${rowId}/`,
      reRunExperimentColumn: (experimentId, colId) =>
        legacyApiPath(
          "/model-hub/experiments/{experiment_id}/re-run/{col_id}/",
          { experiment_id: experimentId, col_id: colId },
          "Legacy experiment rerun column API is not present in backend URLs.",
        ),
      reRunExperimentCell: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/rerun-cells/`,
      suggestName: (datasetId) =>
        `/model-hub/experiments/v2/suggest-name/${datasetId}/`,
      validateName: apiPath("/model-hub/experiments/v2/validate-name/"),
      getExperimentJSONSchema: (expId) =>
        `/model-hub/experiments/v2/${expId}/json-schema/`,
      getExperimentDerivedVariables: (expId) =>
        `/model-hub/experiments/v2/${expId}/derived-variables/`,
      feedback: {
        getTemplate: (experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/get-template/`,
        create: (experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/`,
        getDetails: (experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/get-feedback-details/`,
        submit: (experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/submit-feedback/`,
      },
    },
    apiKey: {
      create: apiPath("/model-hub/api-keys/"),
      status: apiPath("/model-hub/develops/provider-status/"),
      update: apiPath("/model-hub/api-keys/"),
      delete: (id) => `/model-hub/api-keys/${id}/`,
    },
  },
  stripe: {
    createCheckoutSession: apiPath("/usage/create-checkout-session/"),
    cancelSubscription: apiPath("/usage/cancel-subscription/"),
    subscriptionStatus: apiPath("/usage/subscription-status/"),
    subscriptionPlanStatus: apiPath("/usage/subscription-plans/"),
    pricingCardDetails: apiPath("/usage/pricing-card-details/"),
    createCustomPaymentCheckoutSession: apiPath(
      "/usage/create-custom-payment-checkout-session/",
    ),
    getWalletBalance: apiPath("/usage/get-wallet-balance/"),
    getCustomerInvoices: apiPath("/usage/get-customer-invoices/"),
    getBillingDetails: apiPath("/usage/get-billing-details/"),
    updateBillingDetails: apiPath("/usage/update-billing-details/"),
    getAPICallCount: apiPath("/usage/api-call-count/"),
    resendInvitationEmails: apiPath("/accounts/resend-invitation-emails/"),
    deleteUsers: apiPath("/accounts/delete-users/"),
    updateUser: apiPath("/accounts/update-user/"),
    getUserProfileDetails: apiPath("/accounts/get-user-profile-details/"),
    updateUserFullName: apiPath("/accounts/update-user-full-name/"),
    createBillingPortalSession: apiPath(
      "/usage/create-billing-portal-session/",
    ),
    createAutoRechargeSession: apiPath("/usage/create-auto-recharge-session/"),
    createTopupSession: legacyApiPath(
      "/usage/create-topup-session/",
      "Legacy top-up billing API is not exposed in Swagger yet.",
    ),
    getLast4Digits: apiPath("/usage/get-last-four-digits/"),
    updateAutoReloadSettings: apiPath("/usage/update-auto-reload-settings/"),
    getAutoReloadSettings: apiPath("/usage/get-auto-reload-settings/"),
    downloadInvoice: apiPath("/usage/download-invoice/"),
  },
  project: {
    projectExperimentList: apiPath("/tracer/project/"),
    projectObserveList: apiPath("/tracer/project/list_projects/"),
    updateProject: apiPath("/tracer/project/update_project_name/"),
    projectSessionList: () => apiPath("/tracer/trace-session/list_sessions/"),
    projectSessionListExport: apiPath(
      "/tracer/trace-session/get_trace_session_export_data/",
    ),
    updateSessionListColumnVisibility: () =>
      apiPath("/tracer/project/update_project_session_config/"),
    traceSession: apiPath("/tracer/trace-session/"),
    projectExperimentDetail: (projectId) =>
      apiPath("/tracer/project/{id}/", { id: projectId }),
    deleteObservePrototype: apiPath("/tracer/project/"),
    updateProjectName: () => apiPath("/tracer/project/update_project_name/"),
    projectExperimentRun: () => apiPath("/tracer/project-version/list_runs/"),
    updateProjectColumnVisibility: () =>
      apiPath("/tracer/project/update_project_config/"),
    updateProjectVersionColumnVisibility: () =>
      apiPath("/tracer/project-version/update_project_version_config/"),
    chooseWinner: () =>
      apiPath("/tracer/project-version/project_version_winner/"),
    deleteRuns: () => apiPath("/tracer/project-version/delete_runs/"),
    exportRuns: () => apiPath("/tracer/project-version/get_export_data/"),
    runListSearch: () =>
      apiPath("/tracer/project-version/get_project_version_ids/"),
    compareTraces: apiPath("/tracer/trace/compare_traces/"),
    getTrace: (traceId) => apiPath("/tracer/trace/{id}/", { id: traceId }),
    getTraceList: () => apiPath("/tracer/trace/list_traces/"),
    getSpanList: () => apiPath("/tracer/observation-span/list_spans/"),
    getProjectById: (id) => apiPath("/tracer/project/{id}/", { id }),
    getProjectVersion: (runId) => `/tracer/project-version/${runId}/`,
    getProjectVersionInsight: () =>
      apiPath("/tracer/project-version/get_run_insights/"),
    createLabel: () => apiPath("/model-hub/annotations-labels/"),
    updateLabel: (id) => apiPath("/model-hub/annotations-labels/{id}/", { id }),
    deleteLabel: () =>
      apiPath("/tracer/observation-span/delete_annotation_label/"),
    getAnnotationLabels: () => apiPath("/model-hub/annotations-labels/"),
    saveAnnotationLabel: () =>
      apiPath("/tracer/project-version/add_annotations/"),
    getTraceIdByIndex: () => apiPath("/tracer/trace/get_trace_id_by_index/"),
    addAnnotationValues: () =>
      apiPath("/tracer/observation-span/add_annotations/"),
    getTraceIdByIndexObserve: (observeId) =>
      `${apiPath("/tracer/trace/get_trace_id_by_index_observe/")}?project_id=${observeId}`,
    getTraceIdByIndexSpansAsBase: () =>
      apiPath("/tracer/observation-span/get_trace_id_by_index_spans_as_base/"),
    getTraceIdByIndexSpansAsObserve: (observeId) =>
      `${apiPath("/tracer/observation-span/get_trace_id_by_index_spans_as_observe/")}?project_id=${observeId}`,
    addAnnotationValuesForSpan: () =>
      apiPath("/tracer/observation-span/add_annotations/"),
    getObservationSpan: (id) =>
      apiPath("/tracer/observation-span/{id}/", { id }),
    getObservationSpanLoading: (id) =>
      `${apiPath("/tracer/observation-span/retrieve_loading/")}?observation_span_id=${id}`,
    getTracesForObserveProject: () =>
      apiPath("/tracer/trace/list_traces_of_session/"),
    getAgentGraph: () => apiPath("/tracer/trace/agent_graph/"),
    getTraceForObserveExport: apiPath("/tracer/trace/get_trace_export_data/"),
    getSpansForObserveProject: () =>
      apiPath("/tracer/observation-span/list_spans_observe/"),
    getSpansForObserveExport: apiPath(
      "/tracer/observation-span/get_spans_export_data/",
    ),
    getTraceProperties: apiPath("/tracer/trace/get_properties/"),
    getTraceEvals: () => apiPath("/tracer/trace/get_eval_names/"),
    getTraceErrorAnalysis: (id) => `/tracer/trace-error-analysis/${id}/`,
    getTraceGraphData: () => apiPath("/tracer/trace/get_graph_methods/"),
    getSessionGraphData: () =>
      apiPath("/tracer/trace-session/get_session_graph_data/"),
    getSessionFilterValues: () =>
      apiPath("/tracer/trace-session/get_session_filter_values/"),
    getSpanGraphData: () =>
      apiPath("/tracer/observation-span/get_graph_methods/"),
    getEvalTaskList: () => apiPath("/tracer/eval-task/list_eval_tasks/"),
    getEvalTasksWithProjectName: () =>
      apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    markEvalsDeleted: () =>
      apiPath("/tracer/eval-task/mark_eval_tasks_deleted/"),
    updateEvalTask: (id) => `/tracer/eval-task/${id}/`,
    listEvalsWithProject: () =>
      apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    listProjects: () => apiPath("/tracer/project/list_project_ids/"),
    showCharts: () => apiPath("/tracer/project/get_graph_data/"),
    getMonitorList: () => apiPath("/tracer/user-alerts/list_monitors/"),
    getMonitorLogs: (id) =>
      legacyApiPath(
        "/tracer/user-alerts/{id}/fetch_logs/",
        { id },
        "Legacy monitor logs action is not exposed in Swagger yet.",
      ),
    getMonitorMetricList: () => `/tracer/user-alerts/get_metric_details/`,
    duplicateMonitorList: () => `/tracer/user-alerts/duplicate/`,
    createMonitor: apiPath("/tracer/user-alerts/"),
    getMonitorGraph: () => `/tracer/user-alerts/create_graph/`,
    getEvalAttributeList: () =>
      apiPath("/tracer/observation-span/get_eval_attributes_list/"),
    submitFeedback: apiPath("/tracer/observation-span/submit_feedback/"),
    applySubmitFeedback: apiPath(
      "/tracer/observation-span/submit_feedback_action_type/",
    ),
    getEvalDetails: (observationSpanId, customEvalConfigId) =>
      `${apiPath("/tracer/observation-span/get_evaluation_details/")}?custom_eval_config_id=${customEvalConfigId}&observation_span_id=${observationSpanId}`,
    createEvalTask: () => apiPath("/tracer/eval-task/"),
    getEvalTaskDetails: (id) =>
      `/tracer/eval-task/get_eval_details/?eval_id=${id}`,
    patchEvalTask: () => apiPath("/tracer/eval-task/update_eval_task/"),
    getEvalTaskLogs: () => apiPath("/tracer/eval-task/get_eval_task_logs/"),
    getEvalTaskUsage: () => apiPath("/tracer/eval-task/get_usage/"),
    getSessionEvalLogs: (sessionId) =>
      apiPath("/tracer/trace-session/{id}/eval_logs/", { id: sessionId }),
    createEvalTaskConfig: () => apiPath("/tracer/custom-eval-config/"),
    updateEvalTaskConfig: (id) => `/tracer/custom-eval-config/${id}/`,
    getEvalTaskConfig: () =>
      apiPath("/tracer/custom-eval-config/list_custom_eval_configs/"),
    pauseEvalTask: (id) =>
      `/tracer/eval-task/pause_eval_task/?eval_task_id=${id}`,
    resumeEvalTask: (id) =>
      `/tracer/eval-task/unpause_eval_task/?eval_task_id=${id}`,
    getAnnotationsForSpanId: () =>
      apiPath("/tracer/trace-annotation/get_annotation_values/"),
    getObservationSpanField: apiPath(
      "/tracer/observation-span/get_observation_span_fields/",
    ),
    addExistingDataset: apiPath("/tracer/dataset/add_to_existing_dataset/"),
    addNewDataset: apiPath("/tracer/dataset/add_to_new_dataset/"),
    reRunTracerEvalutation: apiPath(
      "/tracer/custom-eval-config/run_evaluation/",
    ),
    getCodeBlockTracer: apiPath("/tracer/project/project_sdk_code/"),
    getEvalGraph: apiPath("/tracer/charts/fetch_graph/"),
    getSystemMetricList: apiPath("/tracer/project/fetch_system_metrics/"),
    muteAlerts: apiPath("/tracer/user-alerts/bulk-mute/"),
    resolveAlerts: apiPath("/tracer/user-alert-logs/resolve/"),
    getAlertDetails: (alertId) => `/tracer/user-alerts/${alertId}/details/`,
    getAlertGraph: (alertId) => `/tracer/user-alerts/${alertId}/graph/`,
    getAlertGraphPreview: apiPath("/tracer/user-alerts/preview-graph/"),
    getUserExampleCode: () => apiPath("/tracer/users/get_code_example/"),
    getUsersList: () => apiPath("/tracer/users/"),
    getUserGraphData: () => apiPath("/tracer/project/get_user_graph_data/"),
    getUsersAggregateGraphData: () =>
      apiPath("/tracer/project/get_users_aggregate_graph_data/"),
    getUserMetrics: () => apiPath("/tracer/project/get_user_metrics/"),
    getCallLogs: apiPath("/tracer/trace/list_voice_calls/"),
    getVoiceCallDetail: apiPath("/tracer/trace/voice_call_detail/"),

    // replay sessions
    prefetchAgentData: `/tracer/replay-session/prefetch-agent-data/`,
    getEvalConfigs: apiPath("/tracer/replay-session/eval-configs/"),
    replaySession: apiPath("/tracer/replay-session/"),
    generateReplayScenarios: (id) =>
      `/tracer/replay-session/${id}/generate-scenario/`,

    // Span Attribute Discovery (ClickHouse)
    spanAttributeKeys: () => apiPath("/api/traces/span-attribute-keys/"),
    spanAttributeValues: () => apiPath("/api/traces/span-attribute-values/"),
    spanAttributeDetail: () => apiPath("/api/traces/span-attribute-detail/"),
    clickhouseHealth: apiPath("/api/health/clickhouse/"),
  },
  row: {
    addRowSdk: apiPath("/model-hub/develops/add_rows_sdk/"),
  },
  misc: {
    uploadFile: apiPath("/model-hub/upload-file/"),
  },
  scenarios: {
    list: apiPath("/simulate/scenarios/"),
    getColumns: apiPath("/simulate/scenarios/get-columns/"),
    create: apiPath("/simulate/scenarios/create/"),
    detail: (id) => `/simulate/scenarios/${id}/`,
    edit: (id) => `/simulate/scenarios/${id}/edit/`,
    delete: (id) => `/simulate/scenarios/${id}/delete/`,
    addRowUsingAi: (scenarioId) =>
      `/simulate/scenarios/${scenarioId}/add-rows/`,
    addCols: (scenarioId) => `/simulate/scenarios/${scenarioId}/add-columns/`,
  },
  simulatorAgents: {
    list: apiPath("/simulate/simulator-agents/"),
    create: apiPath("/simulate/simulator-agents/create/"),
    detail: (id) => `/simulate/simulator-agents/${id}/`,
    edit: (id) => `/simulate/simulator-agents/${id}/edit/`,
    delete: (id) => `/simulate/simulator-agents/${id}/delete/`,
  },
  agentDefinitions: {
    list: apiPath("/simulate/agent-definitions/"),
    create: apiPath("/simulate/agent-definitions/create/"),
    versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
    versionDetail: (id, version) =>
      `/simulate/agent-definitions/${id}/versions/${version}/`,
    createVersion: (id) => `/simulate/agent-definitions/${id}/versions/create/`,
    getCallLogs: (id, version) =>
      `/simulate/agent-definitions/${id}/versions/${version}/call-executions/`,
    detail: (id) => `/simulate/agent-definitions/${id}/`,
    delete: apiPath("/simulate/agent-definitions/"),
    getTestAnalytics: (agent, version) =>
      `/simulate/agent-definitions/${agent}/versions/${version}/eval-summary/`,
    verifyApiKey: apiPath("/tracer/observability-provider/verify_api_key/"),
    verifyAssistantId: apiPath(
      "/tracer/observability-provider/verify_assistant_id/",
    ),
    fetchAssistantFromProvider: apiPath(
      "/simulate/api/agent-definition-operations/fetch_assistant_from_provider/",
    ),
  },
  persona: {
    list: apiPath("/simulate/api/personas/"),
    create: apiPath("/simulate/api/personas/"),
    update: (id) => `/simulate/api/personas/${id}/`,
    delete: (id) => `/simulate/api/personas/${id}/`,
    duplicate: (id) => `/simulate/api/personas/duplicate/${id}/`,
  },
  runTests: {
    list: apiPath("/simulate/run-tests/"),
    create: apiPath("/simulate/run-tests/create/"),
    detail: (id) => `/simulate/run-tests/${id}/`,
    detailExecutions: (id) => `/simulate/run-tests/${id}/executions/`,
    detailScenarios: (id) => `/simulate/run-tests/${id}/scenarios/`,
    runTest: (id) =>
      legacyApiPath(
        "/simulate/run-tests/{id}/execute/",
        { id },
        "Legacy run-test execute API is not exposed in Swagger yet.",
      ),
    callExecutionDetail: (id) => `/simulate/call-executions/${id}/`,
    callExecutionsByTestRunId: (id) =>
      `/simulate/run-tests/${id}/call-executions/`,
    callExecutionsExport: (id) => `/simulate/export/${id}/?type=runtest`,
    executionDetailsExport: (id) =>
      `/simulate/export/${id}/?type=testexecution`,
    addEvals: (testId) => `/simulate/run-tests/${testId}/eval-configs/`,
    deleteEvals: (testId, evalConfigId) =>
      `/simulate/run-tests/${testId}/eval-configs/${evalConfigId}/`,
    updateTestRun: (testId) => `/simulate/run-tests/${testId}/components/`,
    runEvals: (testId) => `/simulate/run-tests/${testId}/run-new-evals/`,
    getConfiguredEvalTemplateConfig: (testId, evalConfigId) =>
      `/simulate/run-tests/${testId}/eval-configs/${evalConfigId}/get-structure/`,
    updateSimulateEval: (testId, evalConfigId) =>
      `/simulate/run-tests/${testId}/eval-configs/${evalConfigId}/update/`,
    getVoiceSDKCode: (testId) => `/simulate/run-tests/${testId}/sdk-code/`,
    deleteSimulation: (testId) =>
      `/simulate/run-tests/${testId}/delete-test-executions/`,
    rerunSimulation: (testId) =>
      `/simulate/run-tests/${testId}/rerun-test-executions/`,
  },
  testExecutions: {
    callDetail: (id) => `/simulate/call-executions/${id}/`,
    list: (id) => `/simulate/test-executions/${id}/`,
    kpis: (id) => `/simulate/test-executions/${id}/kpis/`,
    executionPerformanceSummary: (executionId) =>
      `/simulate/test-executions/${executionId}/performance-summary/`,
    executionAnalytics: (testId) =>
      `/simulate/run-tests/${testId}/eval-summary/`,
    criticalIssue: (executionId) =>
      `/simulate/test-executions/${executionId}/eval-explanation-summary/`,
    criticalIssueRefresh: (executionId) =>
      `/simulate/test-executions/${executionId}/eval-explanation-summary/refresh/`,
    compareSummary: (testId) =>
      `/simulate/run-tests/${testId}/eval-summary-comparison/`,
    flowAnalysis: (executionId) =>
      `/simulate/call-executions/${executionId}/branch-analysis/`,
    cancelExecution: (id) => `/simulate/test-executions/${id}/cancel/`,
    rerunExecution: (id) => `/simulate/test-executions/${id}/rerun-calls/`,
    getDetailLogs: (id) => `/simulate/call-executions/${id}/logs/`,
    getErrorLocalizerTasks: (id) =>
      `/simulate/call-executions/${id}/error-localizer-tasks/`,
    getOptimizerAnalysis: (id) =>
      `/simulate/test-executions/${id}/optimiser-analysis/`,
    refreshOptimizerAnalysis: (id) =>
      `/simulate/test-executions/${id}/optimiser-analysis/refresh/`,
    compareExecutions: (id) =>
      `/simulate/call-executions/${id}/session-comparison/`,
  },
  optimizeSimulate: {
    createOptimization: apiPath("/simulate/api/agent-prompt-optimiser/"),
    getOptimizationDetails: (id) =>
      apiPath("/simulate/api/agent-prompt-optimiser/{id}/", { id }),
    getOptimizationSteps: (id) =>
      `/simulate/api/agent-prompt-optimiser/${id}/steps/`,
    getOptimizationGraph: (id) =>
      `/simulate/api/agent-prompt-optimiser/${id}/graph/`,
    getTrailPrompts: (id, trialId) =>
      `/simulate/api/agent-prompt-optimiser/${id}/trial/${trialId}/prompt/`,
    getTrialItems: (id, trialId) =>
      `/simulate/api/agent-prompt-optimiser/${id}/trial/${trialId}/scenarios/`,
    getOptimizationRuns: () => apiPath("/simulate/api/agent-prompt-optimiser/"),
  },
  workspaces: {
    list: apiPath("/accounts/workspace/list/"),
    create: apiPath("/accounts/workspaces/"),
    switch: apiPath("/accounts/workspace/switch/"),
  },
  dashboard: {
    list: apiPath("/tracer/dashboard/"),
    create: apiPath("/tracer/dashboard/"),
    detail: (id) => apiPath("/tracer/dashboard/{id}/", { id }),
    update: (id) => apiPath("/tracer/dashboard/{id}/", { id }),
    delete: (id) => apiPath("/tracer/dashboard/{id}/", { id }),
    query: apiPath("/tracer/dashboard/query/"),
    metrics: apiPath("/tracer/dashboard/metrics/"),
    filterValues: apiPath("/tracer/dashboard/filter_values/"),
    simulationAgents: apiPath("/tracer/dashboard/simulation-agents/"),
    widgets: (dashboardId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
        dashboard_pk: dashboardId,
      }),
    widgetDetail: (dashboardId, widgetId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
        dashboard_pk: dashboardId,
        id: widgetId,
      }),
    widgetQuery: (dashboardId, widgetId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/", {
        dashboard_pk: dashboardId,
        id: widgetId,
      }),
    widgetPreview: (dashboardId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/preview/", {
        dashboard_pk: dashboardId,
      }),
    widgetReorder: (dashboardId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/reorder/", {
        dashboard_pk: dashboardId,
      }),
    widgetDuplicate: (dashboardId, widgetId) =>
      apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/duplicate/", {
        dashboard_pk: dashboardId,
        id: widgetId,
      }),
  },
  savedViews: {
    list: apiPath("/tracer/saved-views/"),
    create: apiPath("/tracer/saved-views/"),
    detail: (id) => `/tracer/saved-views/${id}/`,
    update: (id) => `/tracer/saved-views/${id}/`,
    delete: (id) => `/tracer/saved-views/${id}/`,
    duplicate: (id) => `/tracer/saved-views/${id}/duplicate/`,
    reorder: apiPath("/tracer/saved-views/reorder/"),
  },
  sharedLinks: {
    list: apiPath("/tracer/shared-links/"),
    create: apiPath("/tracer/shared-links/"),
    detail: (id) => `/tracer/shared-links/${id}/`,
    update: (id) => `/tracer/shared-links/${id}/`,
    delete: (id) => `/tracer/shared-links/${id}/`,
    addAccess: (id) => `/tracer/shared-links/${id}/access/`,
    removeAccess: (id, accessId) =>
      `/tracer/shared-links/${id}/access/${accessId}/`,
    resolve: (token) => `/tracer/shared/${token}/`,
  },
  organizations: {
    list: apiPath("/accounts/organizations/"),
    switch: apiPath("/accounts/organizations/switch/"),
    current: apiPath("/accounts/organizations/current/"),
    create: apiPath("/accounts/organizations/new/"),
    update: apiPath("/accounts/organizations/update/"),
  },
  feed: {
    getFeed: legacyApiPath(
      "/tracer/trace-error-analysis/clusters/feed/",
      "Deprecated trace error cluster API is commented out in backend URLs.",
    ),
    getFeedDetails: (id) =>
      legacyApiPath(
        "/tracer/trace-error-analysis/clusters/{id}/",
        { id },
        "Deprecated trace error cluster API is commented out in backend URLs.",
      ),
  },
  errorFeed: {
    list: apiPath("/tracer/feed/issues/"),
    stats: apiPath("/tracer/feed/issues/stats/"),
    detail: (clusterId) => `/tracer/feed/issues/${clusterId}/`,
    update: (clusterId) => `/tracer/feed/issues/${clusterId}/`,
    overview: (clusterId) => `/tracer/feed/issues/${clusterId}/overview/`,
    traces: (clusterId) => `/tracer/feed/issues/${clusterId}/traces/`,
    trends: (clusterId) => `/tracer/feed/issues/${clusterId}/trends/`,
    sidebar: (clusterId) => `/tracer/feed/issues/${clusterId}/sidebar/`,
    rootCause: (clusterId) => `/tracer/feed/issues/${clusterId}/root-cause/`,
    deepAnalysis: (clusterId) =>
      `/tracer/feed/issues/${clusterId}/deep-analysis/`,
    createLinearIssue: (clusterId) =>
      `/tracer/feed/issues/${clusterId}/create-linear-issue/`,
    linearTeams: apiPath("/tracer/feed/integrations/linear/teams/"),
  },
  promptSimulation: {
    scenarios: apiPath("/simulate/prompt-simulations/scenarios/"),
    simulations: (promptTemplateId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/`,
    detail: (promptTemplateId, runTestId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/${runTestId}/`,
    execute: (promptTemplateId, runTestId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/${runTestId}/execute/`,
  },
  gateway: {
    list: apiPath("/agentcc/gateways/"),
    detail: (id) => `/agentcc/gateways/${id}/`,
    update: (id) => `/agentcc/gateways/${id}/`,
    healthCheck: (id) => `/agentcc/gateways/${id}/health_check/`,
    config: (id) => `/agentcc/gateways/${id}/config/`,
    providers: (id) => `/agentcc/gateways/${id}/providers/`,
    reload: (id) => `/agentcc/gateways/${id}/reload/`,
    updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    updateProvider: (id) => `/agentcc/gateways/${id}/update-provider/`,
    removeProvider: (id) => `/agentcc/gateways/${id}/remove-provider/`,
    testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
    toggleGuardrail: (id) => `/agentcc/gateways/${id}/toggle-guardrail/`,
    updateGuardrail: (id) => `/agentcc/gateways/${id}/update-guardrail/`,
    protectTemplates: apiPath("/agentcc/gateways/protect-templates/"),
    setBudget: (id) => `/agentcc/gateways/${id}/set-budget/`,
    removeBudget: (id) => `/agentcc/gateways/${id}/remove-budget/`,
    mcpStatus: (id) => `/agentcc/gateways/${id}/mcp-status/`,
    mcpTools: (id) => `/agentcc/gateways/${id}/mcp-tools/`,
    updateMcpServer: (id) => `/agentcc/gateways/${id}/update-mcp-server/`,
    removeMcpServer: (id) => `/agentcc/gateways/${id}/remove-mcp-server/`,
    updateMcpGuardrails: (id) =>
      `/agentcc/gateways/${id}/update-mcp-guardrails/`,
    testMcpTool: (id) => `/agentcc/gateways/${id}/test-mcp-tool/`,
    mcpResources: (id) => `/agentcc/gateways/${id}/mcp-resources/`,
    mcpPrompts: (id) => `/agentcc/gateways/${id}/mcp-prompts/`,
    apiKeys: apiPath("/agentcc/api-keys/"),
    createApiKey: apiPath("/agentcc/api-keys/"),
    apiKeyDetail: (id) => `/agentcc/api-keys/${id}/`,
    updateApiKey: (id) => `/agentcc/api-keys/${id}/`,
    revokeApiKey: (id) => `/agentcc/api-keys/${id}/revoke/`,
    syncApiKeys: apiPath("/agentcc/api-keys/sync/"),
    requestLogs: apiPath("/agentcc/request-logs/"),
    requestLogDetail: (id) => `/agentcc/request-logs/${id}/`,
    requestLogSearch: apiPath("/agentcc/request-logs/search/"),
    requestLogSessions: apiPath("/agentcc/request-logs/sessions/"),
    requestLogSessionDetail: (sessionId) =>
      `/agentcc/request-logs/sessions/${sessionId}/`,
    requestLogExport: apiPath("/agentcc/request-logs/export/"),
    analyticsOverview: apiPath("/agentcc/analytics/overview/"),
    analyticsUsage: apiPath("/agentcc/analytics/usage-timeseries/"),
    analyticsCost: apiPath("/agentcc/analytics/cost-breakdown/"),
    analyticsLatency: apiPath("/agentcc/analytics/latency-stats/"),
    analyticsErrors: apiPath("/agentcc/analytics/error-breakdown/"),
    analyticsModels: apiPath("/agentcc/analytics/model-comparison/"),
    orgConfig: {
      list: apiPath("/agentcc/org-configs/"),
      active: apiPath("/agentcc/org-configs/active/"),
      create: apiPath("/agentcc/org-configs/"),
      detail: (cfgId) => `/agentcc/org-configs/${cfgId}/`,
      activate: (cfgId) => `/agentcc/org-configs/${cfgId}/activate/`,
      diff: (cfgId) => `/agentcc/org-configs/${cfgId}/diff/`,
    },
    webhooks: {
      list: apiPath("/agentcc/webhooks/"),
      create: apiPath("/agentcc/webhooks/"),
      detail: (id) => `/agentcc/webhooks/${id}/`,
      update: (id) => `/agentcc/webhooks/${id}/`,
      delete: (id) => `/agentcc/webhooks/${id}/`,
      test: (id) => `/agentcc/webhooks/${id}/test/`,
    },
    webhookEvents: {
      list: apiPath("/agentcc/webhook-events/"),
      detail: (id) => `/agentcc/webhook-events/${id}/`,
      retry: (id) => `/agentcc/webhook-events/${id}/retry/`,
    },
    guardrailFeedback: {
      list: apiPath("/agentcc/guardrail-feedback/"),
      create: apiPath("/agentcc/guardrail-feedback/"),
      detail: (id) => `/agentcc/guardrail-feedback/${id}/`,
      summary: apiPath("/agentcc/guardrail-feedback/summary/"),
    },
    guardrailAnalytics: {
      overview: apiPath("/agentcc/analytics/guardrail-overview/"),
      rules: apiPath("/agentcc/analytics/guardrail-rules/"),
      trends: apiPath("/agentcc/analytics/guardrail-trends/"),
    },
    sessions: {
      list: apiPath("/agentcc/sessions/"),
      create: apiPath("/agentcc/sessions/"),
      detail: (id) => `/agentcc/sessions/${id}/`,
      update: (id) => `/agentcc/sessions/${id}/`,
      delete: (id) => `/agentcc/sessions/${id}/`,
      close: (id) => `/agentcc/sessions/${id}/close/`,
      requests: (id) => `/agentcc/sessions/${id}/requests/`,
    },
    batch: {
      submit: (id) => `/agentcc/gateways/${id}/submit-batch/`,
      get: (id) => `/agentcc/gateways/${id}/get-batch/`,
      cancel: (id) => `/agentcc/gateways/${id}/cancel-batch/`,
    },
    customProperties: {
      list: apiPath("/agentcc/custom-properties/"),
      create: apiPath("/agentcc/custom-properties/"),
      detail: (id) => `/agentcc/custom-properties/${id}/`,
      update: (id) => `/agentcc/custom-properties/${id}/`,
      delete: (id) => `/agentcc/custom-properties/${id}/`,
      validate: apiPath("/agentcc/custom-properties/validate/"),
    },
    emailAlerts: {
      list: apiPath("/agentcc/email-alerts/"),
      create: apiPath("/agentcc/email-alerts/"),
      detail: (id) => `/agentcc/email-alerts/${id}/`,
      update: (id) => `/agentcc/email-alerts/${id}/`,
      delete: (id) => `/agentcc/email-alerts/${id}/`,
      test: (id) => `/agentcc/email-alerts/${id}/test/`,
    },
    shadowExperiments: {
      list: apiPath("/agentcc/shadow-experiments/"),
      create: apiPath("/agentcc/shadow-experiments/"),
      detail: (id) => `/agentcc/shadow-experiments/${id}/`,
      update: (id) => `/agentcc/shadow-experiments/${id}/`,
      delete: (id) => `/agentcc/shadow-experiments/${id}/`,
      pause: (id) => `/agentcc/shadow-experiments/${id}/pause/`,
      resume: (id) => `/agentcc/shadow-experiments/${id}/resume/`,
      complete: (id) => `/agentcc/shadow-experiments/${id}/complete/`,
      stats: (id) => `/agentcc/shadow-experiments/${id}/stats/`,
    },
    shadowResults: {
      list: apiPath("/agentcc/shadow-results/"),
      detail: (id) => `/agentcc/shadow-results/${id}/`,
    },
    providerCredentials: {
      fetchModels: apiPath("/agentcc/provider-credentials/fetch_models/"),
    },
  },
  integrations: {
    connections: {
      list: apiPath("/integrations/connections/"),
      create: apiPath("/integrations/connections/"),
      detail: (id) => `/integrations/connections/${id}/`,
      update: (id) => `/integrations/connections/${id}/`,
      delete: (id) => `/integrations/connections/${id}/`,
      syncNow: (id) => `/integrations/connections/${id}/sync_now/`,
      pause: (id) => `/integrations/connections/${id}/pause/`,
      resume: (id) => `/integrations/connections/${id}/resume/`,
    },
    validate: apiPath("/integrations/connections/validate/"),
    syncLogs: apiPath("/integrations/sync-logs/"),
  },
  agentPlayground: {
    listGraphs: apiPath("/agent-playground/graphs/"),
    createGraph: apiPath("/agent-playground/graphs/"),
    createGraphFromTrace: apiPath("/agent-playground/graphs/from-trace/"),
    deleteGraphs: apiPath("/agent-playground/graphs/delete/"),
    graphDetail: (id) => `/agent-playground/graphs/${id}/`,
    updateGraph: (id) => `/agent-playground/graphs/${id}/`,
    graphVersions: (id) => `/agent-playground/graphs/${id}/versions/`,
    nodeTemplates: apiPath("/agent-playground/node-templates/"),
    versionDetail: (graphId, versionId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/`,
    referenceableGraphs: (id) =>
      `/agent-playground/graphs/${id}/referenceable-graphs/`,
    activateVersion: (graphId, versionId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/activate/`,
    graphDataset: (graphId, versionId) =>
      `/agent-playground/graphs/${graphId}/dataset/?version_id=${versionId}`,
    datasetCell: (graphId, cellId) =>
      `/agent-playground/graphs/${graphId}/dataset/cells/${cellId}/`,
    executeDataset: (graphId) =>
      `/agent-playground/graphs/${graphId}/dataset/execute/`,
    executionDetail: (graphId, executionId) =>
      `/agent-playground/graphs/${graphId}/executions/${executionId}/`,
    nodeExecutionDetail: (executionId, nodeExecutionId) =>
      `/agent-playground/executions/${executionId}/nodes/${nodeExecutionId}/`,
    graphExecutions: (graphId) =>
      `/agent-playground/graphs/${graphId}/executions/`,
    addNode: (graphId, versionId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/`,
    updateNode: (graphId, versionId, nodeId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/${nodeId}/`,
    updatePort: (graphId, versionId, portId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/ports/${portId}/`,
    getNodeDetail: (graphId, versionId, nodeId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/${nodeId}/`,
    createConnection: (graphId, versionId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/node-connections/`,
    deleteConnection: (graphId, versionId, connectionId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/node-connections/${connectionId}/`,
    deleteNode: (graphId, versionId, nodeId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/${nodeId}/`,
    possibleEdgeMappings: (graphId, versionId, nodeId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/${nodeId}/possible-edge-mappings/`,
  },
  mcp: {
    config: apiPath("/mcp/config/"),
    toolGroups: apiPath("/mcp/config/tool-groups/"),
    sessions: apiPath("/mcp/sessions/"),
    tools: apiPath("/mcp/internal/tools/"),
    oauth: {
      authorize: apiPath("/mcp/oauth/authorize/"),
      consent: apiPath("/mcp/oauth/consent/"),
      approveInfo: apiPath("/mcp/oauth/approve-info/"),
      approve: apiPath("/mcp/oauth/approve/"),
    },
  },
  twoFactor: {
    status: apiPath("/accounts/2fa/status/"),
    totp: {
      setup: apiPath("/accounts/2fa/totp/setup/"),
      confirm: apiPath("/accounts/2fa/totp/confirm/"),
      disable: apiPath("/accounts/2fa/totp/"),
    },
    verify: {
      totp: apiPath("/accounts/2fa/verify/totp/"),
      recovery: apiPath("/accounts/2fa/verify/recovery/"),
      passkeyOptions: apiPath("/accounts/2fa/verify/passkey/options/"),
      passkey: apiPath("/accounts/2fa/verify/passkey/"),
    },
    recoveryCodes: {
      count: apiPath("/accounts/2fa/recovery-codes/"),
      regenerate: apiPath("/accounts/2fa/recovery-codes/regenerate/"),
    },
  },
  passkey: {
    list: apiPath("/accounts/passkeys/"),
    registerOptions: apiPath("/accounts/passkey/register/options/"),
    registerVerify: apiPath("/accounts/passkey/register/verify/"),
    detail: (id) => `/accounts/passkeys/${id}/`,
    authenticateOptions: apiPath("/accounts/passkey/authenticate/options/"),
    authenticateVerify: apiPath("/accounts/passkey/authenticate/verify/"),
  },
  orgPolicy: {
    twoFactor: apiPath("/accounts/organization/2fa-policy/"),
  },
  falconAI: {
    conversations: apiPath("/falcon-ai/conversations/"),
    conversation: (id) => `/falcon-ai/conversations/${id}/`,
    messages: (id) =>
      legacyApiPath(
        "/falcon-ai/conversations/{id}/messages/",
        { id },
        "Legacy Falcon conversation messages API is not exposed in Swagger yet.",
      ),
    feedback: (id) => `/falcon-ai/messages/${id}/feedback/`,
    connectors: apiPath("/falcon-ai/mcp-connectors/"),
    connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
    connectorDiscover: (id) => `/falcon-ai/mcp-connectors/${id}/discover/`,
    connectorTest: (id) => `/falcon-ai/mcp-connectors/${id}/test/`,
    connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
    connectorAuth: (id) => `/falcon-ai/mcp-connectors/${id}/authenticate/`,
    skills: apiPath("/falcon-ai/skills/"),
    skill: (id) => `/falcon-ai/skills/${id}/`,
    fileUpload: apiPath("/falcon-ai/files/upload/"),
    quickAnalysis: apiPath("/falcon-ai/quick-analysis/"),
  },
  imagineAnalysis: {
    trigger: apiPath("/tracer/imagine-analysis/"),
    poll: apiPath("/tracer/imagine-analysis/"),
  },
};

export function createQueryString(params) {
  return Object.keys(params)
    .filter((key) => params[key] != undefined) // Only add params which are not undefined
    .map(
      (key) => encodeURIComponent(key) + "=" + encodeURIComponent(params[key]),
    ) // Encode keys and values
    .join("&"); // Join them into a string
}
