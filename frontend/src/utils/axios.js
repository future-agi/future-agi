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
import { resetUser } from "./Mixpanel";
import logger from "./logger";
import { RESPONSE_CODES } from "./constants";

// ----------------------------------------------------------------------
//
const axiosInstance = axios.create({ baseURL: HOST_API });

// ----------------------------------------------------------------------
// Compatibility bridge: add camelCase aliases alongside snake_case keys in
// API responses. The backend previously used djangorestframework-camel-case
// which converted all snake_case keys to camelCase before sending responses.
// That package was removed for performance reasons, but removing it in a
// single atomic change across ~500 frontend files is impractical. This
// interceptor walks response bodies and for every snake_case key adds a
// camelCase alias pointing to the same value, so both `data.total_rows` and
// `data.totalRows` resolve to the same number. Existing call sites keep
// working while per-file cleanup continues. Remove this once every consumer
// has been migrated to snake_case.
//
// Performance: O(n) on response size, non-recursive for primitives, uses
// `Object.defineProperty` to avoid enumerable duplicates so the bridge does
// not leak into request payloads built from response objects.
// ----------------------------------------------------------------------
const SNAKE_TO_CAMEL_RE = /_([a-z0-9])/g;
function snakeToCamelKey(key) {
  return key.replace(SNAKE_TO_CAMEL_RE, (_, c) => c.toUpperCase());
}

// Fields whose VALUES are user-authored maps (keys are user input, not backend
// schema names). Skip recursion into these so the bridge doesn't synthesize
// phantom aliases like `numOfWords` for a template variable the user named
// `num_of_words`. The outer field itself still gets a camelCase alias — only
// the inner keys of that object are left alone (TH-4375).
const USER_KEYED_MAP_FIELDS = new Set(["variable_names", "mapping", "placeholders", "params", "headers"]);

// Build a camelCase→snake_case lookup table for each object we alias.
function buildAliasTable(obj) {
  const table = {};
  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i];
    if (key.includes("_")) {
      const camel = snakeToCamelKey(key);
      if (camel !== key && !(camel in obj)) {
        table[camel] = key;
      }
    }
  }
  return table;
}

// Add camelCase aliases as NON-ENUMERABLE properties. This means:
//   - Direct property access still works: `data.totalRows` reads the same
//     underlying value as `data.total_rows`.
//   - Key iteration (`Object.keys`, `Object.entries`, `Object.values`,
//     `Object.fromEntries`, `JSON.stringify`, `for...in`) sees ONLY the
//     canonical snake_case keys — no duplicate entries, no double renders,
//     no dynamic-column maps doubling in size.
//   - Spreading response data (`{...res.data}`) does NOT copy the camelCase
//     aliases, so request payloads built from response objects do not leak
//     extra keys to the backend.
//
// Getter/setter is used so that writing to the camelCase alias writes
// through to the snake_case key (keeping both views consistent if the
// consumer mutates either side).
function addCamelAliases(obj, seen) {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== "object") return obj;
  if (seen.has(obj)) return obj;
  seen.add(obj);

  if (Array.isArray(obj)) {
    for (let i = 0; i < obj.length; i += 1) {
      addCamelAliases(obj[i], seen);
    }
    return obj;
  }

  // Skip special object types — don't alias keys on these
  if (obj instanceof Date) return obj;
  if (obj instanceof RegExp) return obj;
  if (typeof FormData !== "undefined" && obj instanceof FormData) return obj;
  if (typeof Blob !== "undefined" && obj instanceof Blob) return obj;
  if (typeof File !== "undefined" && obj instanceof File) return obj;

  // Snapshot keys BEFORE aliasing so we don't iterate our own additions.
  const originalKeys = Object.keys(obj);
  for (let i = 0; i < originalKeys.length; i += 1) {
    const key = originalKeys[i];
    if (USER_KEYED_MAP_FIELDS.has(key)) continue;
    const value = obj[key];
    // Recurse into nested structures so child aliases are set too.
    if (value !== null && typeof value === "object") {
      addCamelAliases(value, seen);
    }
  }

  // Now add ENUMERABLE camelCase aliases. We chose enumerable over
  // non-enumerable because the codebase has many call sites that spread
  // API data into new objects (e.g. `{ ...col, newField: X }`), and
  // non-enumerable aliases silently disappear from those spreads,
  // breaking downstream camelCase reads. With enumerable aliases, spreads
  // preserve both keys at the cost of Object.keys iterations seeing both
  // snake_case and camelCase. Use `canonicalKeys` / `canonicalEntries`
  // from src/utils/utils.js when iterating API data to de-duplicate.
  const aliases = buildAliasTable(obj);
  const aliasKeys = Object.keys(aliases);
  if (aliasKeys.length > 0) {
    for (let i = 0; i < aliasKeys.length; i += 1) {
      const camel = aliasKeys[i];
      const snake = aliases[camel];
      try {
        obj[camel] = obj[snake];
      } catch {
        // ignore read-only / frozen objects
      }
    }
  }
  return obj;
}

// const axiosInstance = axios.create({
//   baseURL: HOST_API,
//   headers: {
//     "ngrok-skip-browser-warning": "32434",
//   },
// });

// Strip camelCase aliases from a request body before sending. With
// ENUMERABLE aliases in responses, spreads and clones preserve both
// snake_case and camelCase versions of each key. Without this strip, the
// backend would receive duplicate fields (e.g. both `created_at` and
// `createdAt`) — harmless for most DRF views but ambiguous and wasteful.
// We delete any camelCase key whose snake_case twin exists with the
// same reference value (i.e. the alias installed by the response
// interceptor, not a legitimate frontend-authored camelCase key).
function stripCamelAliases(obj, seen) {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== "object") return obj;
  if (seen.has(obj)) return obj;
  seen.add(obj);

  if (obj instanceof Date) return obj;
  if (obj instanceof RegExp) return obj;
  if (typeof FormData !== "undefined" && obj instanceof FormData) return obj;
  if (typeof Blob !== "undefined" && obj instanceof Blob) return obj;
  if (typeof File !== "undefined" && obj instanceof File) return obj;

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
    // Detect camelCase key (no underscore, has uppercase letter).
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
          // ignore
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

// Request interceptor: strip camelCase aliases from request bodies so the
// backend only ever sees canonical snake_case keys, even when a response
// object (with enumerable aliases added by the response interceptor) is
// spread into a mutation payload. Operates on a deep clone so the caller's
// data stays intact for re-use in React Query cache, form state, etc.
axiosInstance.interceptors.request.use((config) => {
  try {
    if (
      config &&
      config.data &&
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
    // never break a request because of the bridge
  }
  return config;
});

axiosInstance.interceptors.response.use(
  (res) => {
    // Attach camelCase aliases to response data so existing UI code that
    // still reads `data.createdAt` keeps working alongside `data.created_at`.
    try {
      if (res && res.data) {
        addCamelAliases(res.data, new WeakSet());
      }
    } catch {
      // never break a successful response because of the bridge
    }
    return res;
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
      enqueueSnackbar(
        error.response.data.error || "Not available on OSS. Upgrade your plan.",
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

    // Mirror success-response behaviour: attach camelCase aliases so error
    // handlers can read both snake_case and camelCase keys (e.g. `error_code`
    // vs `errorCode`, `upgrade_cta` vs `upgradeCta`). Keeps error handling
    // code consistent with success-path conventions without a big refactor.
    try {
      addCamelAliases(customError, new WeakSet());
    } catch {
      // never swallow the original error because of the bridge
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
    getTabs: "/accounts/first-checks/",
  },
  overview: {
    dashboardSummary: "/model-hub/overview/",
  },
  keys: {
    keys: "/accounts/keys/",
    getKeys: `/accounts/key/get_secret_keys/`,
    enableKey: `/accounts/key/enable_key/`,
    disablekey: `/accounts/key/disable_key/`,
    deleteKey: `/accounts/key/delete_secret_key/ `,
    generateSecretKey: `/accounts/key/generate_secret_key/`,
  },
  auth: {
    me: "/accounts/user-info/",
    login: "/accounts/token/",
    register: "/accounts/signup/",
    user_onboarding_info: "/accounts/onboarding/",
    passwordResetInitiate: "/accounts/password-reset-initiate/",
    passwordReset: "/accounts/password-reset-confirm/",
    service: (provider) => `/saml2_auth/login/?provider=${provider}`,
    create_org: "/accounts/team/users/",
    ssoLogin: (email) => `/saml2_auth/idp-login/?email=${email}`,
    logout: "/accounts/logout/",
    refreshToken: "/accounts/token/refresh/",
    awsSignUp: "/accounts/aws-marketplace/signup/",
    config: "/accounts/config/",
    createOrganization: "/accounts/organizations/create/",
  },
  workspace: {
    getMembers: (workspace_id) =>
      `/accounts/workspaces/${workspace_id}/members/`,
    userList: `/accounts/user/list/`,
    workspaceList: `/accounts/workspace/list/`,
    updateRole: `/accounts/user/role/update/`,
    resendInvite: `/accounts/user/resend-invite/`,
    deleteUser: `/accounts/user/delete/`,
    workspaceInvite: `/accounts/workspace/invite/`,
    deactivate: `/accounts/user/deactivate/`,
    removeUserFromWrokspace: (workspace_id, member_id) =>
      `/accounts/workspaces/${workspace_id}/members/${member_id}/`,
    workspaceUpdate: (workspace_id) => `/accounts/workspaces/${workspace_id}/`,
  },
  // New RBAC endpoints (Phase 2)
  rbac: {
    inviteCreate: `/accounts/organization/invite/`,
    inviteResend: `/accounts/organization/invite/resend/`,
    inviteCancel: `/accounts/organization/invite/cancel/`,
    memberList: `/accounts/organization/members/`,
    memberRoleUpdate: `/accounts/organization/members/role/`,
    memberRemove: `/accounts/organization/members/remove/`,
    memberReactivate: `/accounts/organization/members/reactivate/`,
    workspaceMemberList: (wsId) => `/accounts/workspace/${wsId}/members/`,
    workspaceMemberRoleUpdate: (wsId) =>
      `/accounts/workspace/${wsId}/members/role/`,
    workspaceMemberRemove: (wsId) =>
      `/accounts/workspace/${wsId}/members/remove/`,
  },
  invite: {
    accept_invitation: `/accounts/accept-invitation/`,
  },
  model: {
    list: "/model-hub/ai-models/",
    details: "/model-hub/ai-models/",
    updateMetric: "/model-hub/ai-models/update-metric/",
    performance: "/model-hub/ai-models/performance",
    create: "/model-hub/ai_models/create/",
    updateDefaultDataset: "/model-hub/ai_models/update-baseline/",
    modelList: "/model-hub/ai-models/list/",
    deleteModel: (id) => `/model-hub/ai_models/delete/${id}/`,
    getModelDetail: `/model-hub/get-model-details/`,
  },
  dataset: {
    list: "/model-hub/dataset/",
    summary: "/model-hub/dataset/summary",
    options: "/model-hub/dataset/options/",
    getColumns: "/model-hub/dataset/column-config/",
    updateColumns: "/model-hub/dataset/column-config/",
    createDataset: "/model-hub/dataset/create/",
    propertyList: "/model-hub/dataset/properties/",
    propertyDetail: (id) => `/model-hub/dataset/properties/${id}/`,
    createProperty: "/model-hub/dataset/properties/",
    promptSummary: (id) => `/model-hub/dataset/${id}/run-prompt-stats/`,
    evalsSummary: (id) => `/model-hub/dataset/${id}/eval-stats/`,
    annotationSummary: (id) => `/model-hub/dataset/${id}/annotation-summary/`,
    baseColumndata: "/model-hub/datasets/get-base-columns/",
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
    getColumns: "/model-hub/data-points/column-config/",
    updateColumns: "/model-hub/data-points/column-config/",
    list: "/model-hub/data-points/",
    create: "/model-hub/data-points/create/",
    metrics: "/model-hub/data-points/metrics/",
  },
  event: {
    names: "/model-hub/event-names/",
    list: "/model-hub/events/",
    uniqueProperties: "/model-hub/unique-properties/",
  },
  annotation: {
    list: "/model-hub/annotation-tasks/",
    annotationLabelText: "/model-hub/annotations-labels/",
    annotationsListByDataSetId: (dataSetId) =>
      "/model-hub/annotations/?dataset=" + dataSetId,
    previewAnnotations: "/model-hub/annotations/preview_annotations/",
    createNewAnnotation: "/model-hub/annotations/",
    getAnnotationById: (id) => `/model-hub/annotations/${id}/`,
    putAnnotationById: `/model-hub/annotations/`,
    annotateRow: (id) => `/model-hub/annotations/${id}/annotate_row/`,
    annotationsUser: (id) => `/model-hub/organizations/${id}/users/`,
    deleteAnnotation: (id) => `/model-hub/annotations/${id}/`,
    deleteAnnotations: `/model-hub/annotations/bulk_destroy/`,
    updateAnnotation: (annotationId) =>
      `/model-hub/annotations/${annotationId}/update_cells/`,
    resetAnnotation: (annotationId) =>
      `/model-hub/annotations/${annotationId}/reset_annotations/`,
  },
  knowledge: {
    knowledgeBase: "/model-hub/knowledge-base/",
    list: "/model-hub/knowledge-base/get/",
    files: "/model-hub/knowledge-base/files/",
  },
  customMetric: {
    list: "/model-hub/custom-metric/",
    create: "/model-hub/custom-metric/create/",
    edit: "/model-hub/custom-metric/update/",
    all: "/model-hub/custom-metric/all/",
    tagOptions: "/model-hub/custom-metric/tag-options/",
    testMetric: "/model-hub/custom-metric/test/",
  },
  performance: {
    graphData: "/model-hub/performance/",
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
    createOptimization: "/model-hub/optimize-dataset/",
    stopOptimization: (id) => `/model-hub/dataset-optimization/${id}/stop/`,
    getAll: "/model-hub/optimize-dataset/",
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
      getMemberList: "/accounts/team/users/",
      deleteMember: (id) => `/accounts/team/users/${id}/`,
      inviteMember: "/accounts/team/users/",
    },
    apiKeys: "/model-hub/api-keys/",
    customModal: {
      getCustomModal: "/model-hub/custom-models/",
      createCustomModal: "/model-hub/custom_models/create/",
      editCustomModel: "/model-hub/custom_models/edit/",
      deleteModel: "/model-hub/custom_models/delete/",
    },
    getLatestPrices: `/usage/get_latest_prices/`,
    getAvailableMonths: `/usage/available-months/`,
    usageTotals: `/usage/workspace-usage-summary/`,
    workspaceUsage: `/usage/workspace-eval-summary/`,
    usageMetrics: `/usage/usage-summary/`,
    v2: {
      usageOverview: `/usage/v2/usage-overview/`,
      usageTimeSeries: `/usage/v2/usage-time-series/`,
      usageWorkspaceBreakdown: `/usage/v2/usage-workspace-breakdown/`,
      plansAndAddons: `/usage/v2/plans-and-addons/`,
      billingOverview: `/usage/v2/billing-overview/`,
      invoices: `/usage/v2/invoices/`,
      invoiceDetail: (id) => `/usage/v2/invoices/${id}/`,
      notifications: `/usage/v2/notifications/`,
      budgets: `/usage/v2/budgets/`,
      budgetDetail: (id) => `/usage/v2/budgets/${id}/`,
      upgradeToPayg: `/usage/v2/upgrade-to-payg/`,
      downgradeToFree: `/usage/v2/downgrade-to-free/`,
      addAddon: `/usage/v2/add-addon/`,
      removeAddon: `/usage/v2/remove-addon/`,
      reinstateAddon: `/usage/v2/reinstate-addon/`,
      paymentMethods: `/usage/v2/payment-methods/`,
      paymentMethodSetupIntent: `/usage/v2/payment-methods/setup-intent/`,
      paymentMethodDefault: (pmId) =>
        `/usage/v2/payment-methods/${pmId}/default/`,
      paymentMethodDelete: (pmId) => `/usage/v2/payment-methods/${pmId}/`,
      deploymentInfo: `/api/deployment-info/`,
    },
  },
  tools: {
    create: "/model-hub/tools/",
    update: (id) => `/model-hub/tools/${id}/`,
  },
  secrets: {
    list: "/model-hub/secrets/",
    create: "/model-hub/secrets/",
  },
  huggingFace: {
    list: "/model-hub/datasets/huggingface/list/",
    detail: "/model-hub/datasets/huggingface/detail/",
    addHuggingFaceRow: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_rows_from_huggingface/`,
  },
  develop: {
    modelList: "/model-hub/api/models_list/",
    modelParams: "/model-hub/api/model_parameters/",
    getDatasets: () => `/model-hub/develops/get-datasets/`,
    getDerivedDatasets: () => `/model-hub/develops/get-derived-datasets/`,
    getDatasetList: () => `/model-hub/develops/get-datasets-names/`,
    getCellData: `/model-hub/develops/get-cell-data/`,
    getRowsDiff: `/model-hub/experiments/v2/row-diff/`,
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
    uploadDatasetLocalFile:
      "/model-hub/develops/create-dataset-from-local-file/",
    uploadDatasetRow: "/model-hub/develops/add_rows_from_file/",
    addEmptyRow: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_empty_rows/`,
    getSyntheticConfig: (datasetId) =>
      `/model-hub/develops/${datasetId}/synthetic-config/`,
    createSyntheticDataset: `/model-hub/develops/create-synthetic-dataset/`,
    updateSyntheticDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/update-synthetic-config/`,
    addSyntheticDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_synthetic_data/ `,
    createDatasetManually: "/model-hub/develops/create-dataset-manually/",
    createEmptyDataset: "/model-hub/develops/create-empty-dataset/",
    getHuggingFaceDataset:
      "/model-hub/develops/get-huggingface-dataset-config/",
    createHuggingFaceDataset:
      "/model-hub/develops/create-dataset-from-huggingface/",
    cloneDataset: (newDatasetId) =>
      `/model-hub/develops/clone-dataset/${newDatasetId}/`,
    createFromExistingDataset: `/model-hub/develops/add-as-new/`,
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
    deleteDataset: () => "/model-hub/develops/delete_dataset/",
    addDatasetColumn: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_columns/`,
    addRowFromExistingDataset: (datasetId) =>
      `/model-hub/develops/${datasetId}/add_rows_from_existing_dataset/`,
    getRowData: (datasetId) => `/model-hub/develops/${datasetId}/get-row-data/`,
    addColumns: {
      apiCall: (datasetId) =>
        `/model-hub/datasets/${datasetId}/add-api-column/`,
      executeCode: (datasetId) =>
        `/model-hub/datasets/${datasetId}/execute-code/`,
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
    evaluateRows: () => `/model-hub/evaluate-rows/`,
    evaluateRunRows: () => `/model-hub/run-prompt-for-rows/`,
    eval: {
      createCustomEval: `/model-hub/create_custom_evals/`,
      getEvalsList: (datasetId) =>
        `/model-hub/develops/${datasetId}/get_evals_list/`,
      getCompareEvalsList: () => `/model-hub/datasets/compare/get-evals-list/`,
      getEvalTemplateConfig: (templateId) =>
        `/model-hub/develops/get_preset_eval_structure/${templateId}/`,
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
      getFunctionEvalsList: `/model-hub/develops/get_function_list/`,
      addFeedback: `/model-hub/feedback/`,
      getFeedbackTemplate: `/model-hub/feedback/get_template/`,
      getFeedbackTemplateTrace: (id) => `/tracer/custom-eval-config/${id}`,
      updateFeedback: `/model-hub/feedback/submit-feedback/`,
      getFeedbackDetails: `/model-hub/feedback/get-feedback-details/`,
      getEvalLogs: `/model-hub/get-eval-logs`,
      runCellErrorLocalizer: (cellId) =>
        `/model-hub/cells/${cellId}/run-error-localizer/`,
      getCellErrorLocalizer: (cellId) =>
        `/model-hub/cells/${cellId}/run-error-localizer/`,
      getEvalsLogs: `/model-hub/get-eval-logs-details`,
      getEvalMetrics: `/model-hub/get-eval-metrics`,
      getEvalFeedbacks: `/model-hub/get-eval-feedback`,
      getEvalTemplates: `/model-hub/get-eval-templates`,
      listEvalTemplates: `/model-hub/eval-templates/list/`,
      listEvalTemplateCharts: `/model-hub/eval-templates/list-charts/`,
      bulkDeleteEvalTemplates: `/model-hub/eval-templates/bulk-delete/`,
      createEvalTemplateV2: `/model-hub/eval-templates/create-v2/`,
      createCompositeEval: `/model-hub/eval-templates/create-composite/`,
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
      executeCompositeEvalAdhoc: `/model-hub/eval-templates/composite/execute-adhoc/`,
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
      runEval: `/model-hub/run-eval`,
      getEvalConfigs: `/model-hub/get-eval-config`,
      getEvalNames: `/model-hub/get-eval-template-names`,
      aiFilter: `/model-hub/ai-filter/`,
      aiEvalWriter: `/model-hub/ai-eval-writer/`,
      summaryTemplates: `/model-hub/eval-summary-templates/`,
      summaryTemplate: (id) => `/model-hub/eval-summary-templates/${id}/`,
      evalPlayground: `/model-hub/eval-playground/`,
      updateEvalsTemplate: `/model-hub/update-eval-template/`,
      testEvaluation: `/model-hub/test-evaluation/`,
      evalPlaygroundLog: `/model-hub/eval-playground-logs/`,
      addEvalsFeedback: `/model-hub/eval-playground/feedback/`,
      duplicateEvalsTemplate: `/model-hub/duplicate-eval-template/`,
      deleteEvalsTemplate: `/model-hub/delete-eval-template/`,
      evalsSDKCode: `/model-hub/eval-sdk-code/`,
      groupEvals: `/model-hub/eval-groups/`,
      editGroupEvalList: `/model-hub/eval-groups/edit-eval-list/`,
      applyEvalGroup: `/model-hub/eval-groups/apply-eval-group/`,
    },
    runPrompt: {
      create: "/model-hub/develops/add_run_prompt_column/",
      preview: "/model-hub/develops/preview_run_prompt_column/",
      runPromptOptions: "/model-hub/develops/retrieve_run_prompt_options/",
      voiceOptions: "/model-hub/api/model_voices/",
      createCustomVoice: "/model-hub/tts-voices/",
      createTemplateId: "/model-hub/prompt-templates/",
      createPromptDraft: `/model-hub/prompt-templates/create-draft/`,
      getPrompt: (id) => `/model-hub/prompt-templates/${id}/`,
      getNameChange: (id) => `/model-hub/prompt-templates/${id}/save-name/`,
      generatePrompt: "/model-hub/prompt-templates/generate-prompt/",
      generateVariables: "/model-hub/prompt-templates/generate-variables/",
      getStatus: (/** @type {string} */ id) =>
        `/model-hub/prompt-templates/${id}/get-run-status/`,
      getPromptVersions: () => `/model-hub/prompt-history-executions/`,
      // https://dev.api.futureagi.com/model-hub/prompt-templates/6b6b4d0b-ef4f-4a8b-82e9-2d2bbaedc6b5/run_template/
      runTemplatePrompt: (id) =>
        `/model-hub/prompt-templates/${id}/run_template/`,
      getRunPrompt: () =>
        `/model-hub/develops/retrieve_run_prompt_column_config/`,
      editRunPrompt: () => `/model-hub/develops/edit_run_prompt_column/`,
      applyVariables: () => `/model-hub/get-column-values/`,
      promptExecutions: () => `/model-hub/prompt-executions/`,
      promptDelete: (id) => `/model-hub/prompt-templates/${id}/`,
      promptMultiDelete: `/model-hub/prompt-templates/bulk-delete/`,
      analyzePrompt: "/model-hub/prompt-templates/analyze-prompt/",
      improvePrompt: "/model-hub/prompt-templates/improve-prompt/",
      updatePrompt: `/model-hub/prompt-templates/improve-prompt/`,
      responseSchema: "/model-hub/response_schema/",
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
      previewDerivedVariables: `/model-hub/prompt-templates/derived-variables/preview/`,
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
      promptLabels: "/model-hub/prompt-labels/",
      createPromptLabel: "/model-hub/prompt-labels/",
      deletePromptLabel: (id) => `/model-hub/prompt-labels/${id}/`,
      assignLabels: (promptId, labelId) =>
        `/model-hub/prompt-labels/${promptId}/${labelId}/assign-label-by-id/`,
      assignMultipleLabels: `/model-hub/prompt-labels/assign-multiple-labels/`,
      removeLabel: () => `/model-hub/prompt-labels/remove/`,
      getPromptMetrics: () => `/model-hub/prompt/metrics/`,
      getPromptSpanMetrics: () => `/model-hub/prompt/span-metrics/`,
      promptMetricEmptyScreen: () => `/model-hub/prompt/metrics/empty-screen`,
      promptFolder: "/model-hub/prompt-folders/",
      promptFolderId: (id) => `/model-hub/prompt-folders/${id}/`,
      movePrompt: (folderId) =>
        `/model-hub/prompt-templates/${folderId}/save-prompt-folder/`,
      promptTemplate: `/model-hub/prompt-base-templates/`,
      promptTemplateId: (id) => `/model-hub/prompt-base-templates/${id}/`,
      categories: `/model-hub/prompt-base-templates/get-all-categories/`,
    },
    optimizeDevelop: {
      columnInfo: "/model-hub/metrics/by-column/",
      create: "/model-hub/optimisation/create/",
      list: "/model-hub/optimisation/",
      detail: (optimizationId) =>
        `/model-hub/optimisation/${optimizationId}/details/`,
    },
    datasetOptimization: {
      create: "/model-hub/dataset-optimization/",
      list: "/model-hub/dataset-optimization/",
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
      index: `/model-hub/experiments/v2/`,
      update: (id) => `/model-hub/experiments/v2/${id}/`,
      create: () => `/model-hub/experiments/v2/`,
      getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
      experimentListPaginated: `/model-hub/experiments/v2/list/`,
      experimentList: "/model-hub/experiment-detail/",
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
      rerun: `/model-hub/experiments/v2/re-run/`,
      delete: `/model-hub/experiments/v2/delete/`,
      stop: (id) => `/model-hub/experiments/v2/${id}/stop/`,
      rowDetail: (experimentId, rowId) =>
        `/model-hub/experiments/${experimentId}/${rowId}/`,
      reRunExperimentColumn: (experimentId, colId) =>
        `/model-hub/experiments/${experimentId}/re-run/${colId}/`,
      reRunExperimentCell: (experimentId) =>
        `/model-hub/experiments/v2/${experimentId}/rerun-cells/`,
      suggestName: (datasetId) =>
        `/model-hub/experiments/v2/suggest-name/${datasetId}/`,
      validateName: `/model-hub/experiments/v2/validate-name/`,
      getExperimentJSONSchema: (expId) =>
        `/model-hub/experiments/v2/${expId}/json-schema/`,
      getExperimentDerivedVariables: (expId) =>
        `/model-hub/experiments/v2/${expId}/derived-variables/`,
      feedback: {
        getTemplate: ( experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/get-template/`,
        create: ( experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/`,
        getDetails: ( experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/get-feedback-details/`,
        submit: ( experimentId) =>
          `/model-hub/experiments/v2/${experimentId}/feedback/submit-feedback/`,
      },
    },
    apiKey: {
      create: "/model-hub/api-keys/",
      status: "/model-hub/develops/provider-status/",
      update: `/model-hub/api-keys/`,
      delete: (id) => `/model-hub/api-keys/${id}/`,
    },
  },
  stripe: {
    createCheckoutSession: "/usage/create-checkout-session/",
    cancelSubscription: "/usage/cancel-subscription/",
    subscriptionStatus: "/usage/subscription-status/",
    subscriptionPlanStatus: "/usage/subscription-plans/",
    pricingCardDetails: "/usage/pricing-card-details/",
    createCustomPaymentCheckoutSession:
      "/usage/create-custom-payment-checkout-session/",
    getWalletBalance: "/usage/get-wallet-balance/",
    getCustomerInvoices: "/usage/get-customer-invoices/",
    getBillingDetails: "/usage/get-billing-details/",
    updateBillingDetails: "/usage/update-billing-details/",
    getAPICallCount: "/usage/api-call-count/",
    resendInvitationEmails: "/accounts/resend-invitation-emails/",
    deleteUsers: "/accounts/delete-users/",
    updateUser: "/accounts/update-user/",
    getUserProfileDetails: "/accounts/get-user-profile-details/",
    updateUserFullName: "/accounts/update-user-full-name/",
    createBillingPortalSession: "/usage/create-billing-portal-session/",
    createAutoRechargeSession: "/usage/create-auto-recharge-session/",
    createTopupSession: "/usage/create-topup-session/",
    getLast4Digits: "/usage/get-last-four-digits/",
    updateAutoReloadSettings: "/usage/update-auto-reload-settings/",
    getAutoReloadSettings: "/usage/get-auto-reload-settings/",
    downloadInvoice: "/usage/download-invoice/",
  },
  project: {
    projectExperimentList: "/tracer/project/",
    projectObserveList: "/tracer/project/list_projects/",
    updateProject: `/tracer/project/update_project_name/`,
    projectSessionList: () => "/tracer/trace-session/list_sessions/",
    projectSessionListExport:
      "/tracer/trace-session/get_trace_session_export_data/",
    updateSessionListColumnVisibility: () =>
      "/tracer/project/update_project_session_config/",
    traceSession: "/tracer/trace-session/",
    projectExperimentDetail: (projectId) => `/tracer/project/${projectId}/`,
    deleteObservePrototype: "/tracer/project/",
    updateProjectName: () => `/tracer/project/update_project_name/`,
    projectExperimentRun: () => `/tracer/project-version/list_runs/`,
    updateProjectColumnVisibility: () =>
      `/tracer/project/update_project_config/`,
    updateProjectVersionColumnVisibility: () =>
      `/tracer/project-version/update_project_version_config/`,
    chooseWinner: () => `/tracer/project-version/project_version_winner/`,
    deleteRuns: () => `/tracer/project-version/delete_runs/`,
    exportRuns: () => `/tracer/project-version/get_export_data/`,
    runListSearch: () => `/tracer/project-version/get_project_version_ids/`,
    compareTraces: "/tracer/trace/compare_traces/",
    getTrace: (traceId) => `/tracer/trace/${traceId}/`,
    getTraceList: () => `/tracer/trace/list_traces/`,
    getSpanList: () => `/tracer/observation-span/list_spans/`,
    getProjectById: (id) => `/tracer/project/${id}/`,
    getProjectVersion: (runId) => `/tracer/project-version/${runId}/`,
    getProjectVersionInsight: () => `/tracer/project-version/get_run_insights/`,
    createLabel: () => `/model-hub/annotations-labels/`,
    updateLabel: (id) => `/model-hub/annotations-labels/${id}/`,
    deleteLabel: () => `/tracer/observation-span/delete_annotation_label/`,
    getAnnotationLabels: () => `/model-hub/annotations-labels/`,
    saveAnnotationLabel: () => `/tracer/project-version/add_annotations/`,
    getTraceIdByIndex: () => `/tracer/trace/get_trace_id_by_index/`,
    addAnnotationValues: () => `/tracer/observation-span/add_annotations/`,
    getTraceIdByIndexObserve: (observeId) =>
      `/tracer/trace/get_trace_id_by_index_observe/?project_id=${observeId}`,
    getTraceIdByIndexSpansAsBase: () =>
      `/tracer/observation-span/get_trace_id_by_index_spans_as_base/`,
    getTraceIdByIndexSpansAsObserve: (observeId) =>
      `/tracer/observation-span/get_trace_id_by_index_spans_as_observe/?project_id=${observeId}`,
    addAnnotationValuesForSpan: () =>
      `/tracer/observation-span/add_annotations/`,
    getObservationSpan: (id) => `/tracer/observation-span/${id}/`,
    getObservationSpanLoading: (id) =>
      `/tracer/observation-span/retrieve_loading/?observation_span_id=${id}`,
    getTracesForObserveProject: () => `/tracer/trace/list_traces_of_session/`,
    getAgentGraph: () => `/tracer/trace/agent_graph/`,
    getTraceForObserveExport: `/tracer/trace/get_trace_export_data/`,
    getSpansForObserveProject: () =>
      `/tracer/observation-span/list_spans_observe/`,
    getSpansForObserveExport: `/tracer/observation-span/get_spans_export_data/`,
    getTraceProperties: `/tracer/trace/get_properties/`,
    getTraceEvals: () => `/tracer/trace/get_eval_names/`,
    getTraceErrorAnalysis: (id) => `/tracer/trace-error-analysis/${id}/`,
    getTraceGraphData: () => `/tracer/trace/get_graph_methods/`,
    getSessionGraphData: () => `/tracer/trace-session/get_session_graph_data/`,
    getSessionFilterValues: () =>
      `/tracer/trace-session/get_session_filter_values/`,
    getSpanGraphData: () => `/tracer/observation-span/get_graph_methods/`,
    getEvalTaskList: () => `/tracer/eval-task/list_eval_tasks/`,
    getEvalTasksWithProjectName: () =>
      `/tracer/eval-task/list_eval_tasks_with_project_name/`,
    markEvalsDeleted: () => `/tracer/eval-task/mark_eval_tasks_deleted/`,
    updateEvalTask: (id) => `/tracer/eval-task/${id}/`,
    listEvalsWithProject: () =>
      `/tracer/eval-task/list_eval_tasks_with_project_name/`,
    listProjects: () => `/tracer/project/list_project_ids/`,
    showCharts: () => `/tracer/project/get_graph_data/`,
    getMonitorList: () => `/tracer/user-alerts/list_monitors/`,
    getMonitorLogs: (id) => `/tracer/user-alerts/${id}/fetch_logs/`,
    getMonitorMetricList: () => `/tracer/user-alerts/get_metric_details/`,
    duplicateMonitorList: () => `/tracer/user-alerts/duplicate/`,
    createMonitor: `/tracer/user-alerts/`,
    getMonitorGraph: () => `/tracer/user-alerts/create_graph/`,
    getEvalAttributeList: () =>
      `/tracer/observation-span/get_eval_attributes_list/`,
    submitFeedback: `/tracer/observation-span/submit_feedback/`,
    applySubmitFeedback: `/tracer/observation-span/submit_feedback_action_type/`,
    getEvalDetails: (observationSpanId, customEvalConfigId) =>
      `/tracer/observation-span/get_evaluation_details?custom_eval_config_id=${customEvalConfigId}&observation_span_id=${observationSpanId}`,
    createEvalTask: () => `/tracer/eval-task/`,
    getEvalTaskDetails: (id) =>
      `/tracer/eval-task/get_eval_details/?eval_id=${id}`,
    patchEvalTask: () => `/tracer/eval-task/update_eval_task/`,
    getEvalTaskLogs: () => `/tracer/eval-task/get_eval_task_logs/`,
    getEvalTaskUsage: () => `/tracer/eval-task/get_usage/`,
    createEvalTaskConfig: () => `/tracer/custom-eval-config/`,
    updateEvalTaskConfig: (id) => `/tracer/custom-eval-config/${id}/`,
    getEvalTaskConfig: () =>
      `/tracer/custom-eval-config/list_custom_eval_configs/`,
    pauseEvalTask: (id) =>
      `/tracer/eval-task/pause_eval_task/?eval_task_id=${id}`,
    resumeEvalTask: (id) =>
      `/tracer/eval-task/unpause_eval_task/?eval_task_id=${id}`,
    getAnnotationsForSpanId: () =>
      `/tracer/trace-annotation/get_annotation_values/`,
    getObservationSpanField: `/tracer/observation-span/get_observation_span_fields/`,
    addExistingDataset: `/tracer/dataset/add_to_existing_dataset/`,
    addNewDataset: `/tracer/dataset/add_to_new_dataset/`,
    reRunTracerEvalutation: `/tracer/custom-eval-config/run_evaluation/`,
    getCodeBlockTracer: `/tracer/project/project_sdk_code/`,
    getEvalGraph: `/tracer/charts/fetch_graph/`,
    getSystemMetricList: "/tracer/project/fetch_system_metrics/",
    muteAlerts: "/tracer/user-alerts/bulk-mute/",
    resolveAlerts: "/tracer/user-alert-logs/resolve/",
    getAlertDetails: (alertId) => `/tracer/user-alerts/${alertId}/details/`,
    getAlertGraph: (alertId) => `/tracer/user-alerts/${alertId}/graph/`,
    getAlertGraphPreview: `/tracer/user-alerts/preview-graph/`,
    getUserExampleCode: () => `/tracer/users/get_code_example/`,
    getUsersList: () => "/tracer/users/",
    getUserGraphData: () => `/tracer/project/get_user_graph_data/`,
    getUsersAggregateGraphData: () =>
      `/tracer/project/get_users_aggregate_graph_data/`,
    getUserMetrics: () => "/tracer/project/get_user_metrics/",
    getCallLogs: `/tracer/trace/list_voice_calls/`,
    getVoiceCallDetail: `/tracer/trace/voice_call_detail/`,

    // replay sessions
    prefetchAgentData: `/tracer/replay-session/prefetch-agent-data/`,
    getEvalConfigs: `/tracer/replay-session/eval-configs/`,
    replaySession: `/tracer/replay-session/`,
    generateReplayScenarios: (id) =>
      `/tracer/replay-session/${id}/generate-scenario/`,

    // Span Attribute Discovery (ClickHouse)
    spanAttributeKeys: () => `/api/traces/span-attribute-keys/`,
    spanAttributeValues: () => `/api/traces/span-attribute-values/`,
    spanAttributeDetail: () => `/api/traces/span-attribute-detail/`,
    clickhouseHealth: `/api/health/clickhouse/`,
  },
  row: {
    addRowSdk: "/model-hub/develops/add_rows_sdk/",
  },
  misc: {
    uploadFile: "/model-hub/upload-file/",
  },
  scenarios: {
    list: "/simulate/scenarios/",
    getColumns: "/simulate/scenarios/get-columns/",
    create: "/simulate/scenarios/create/",
    detail: (id) => `/simulate/scenarios/${id}/`,
    edit: (id) => `/simulate/scenarios/${id}/edit/`,
    delete: (id) => `/simulate/scenarios/${id}/delete/`,
    addRowUsingAi: (scenarioId) =>
      `/simulate/scenarios/${scenarioId}/add-rows/`,
    addCols: (scenarioId) => `/simulate/scenarios/${scenarioId}/add-columns/`,
  },
  simulatorAgents: {
    list: "/simulate/simulator-agents/",
    create: "/simulate/simulator-agents/create/",
    detail: (id) => `/simulate/simulator-agents/${id}/`,
    edit: (id) => `/simulate/simulator-agents/${id}/edit/`,
    delete: (id) => `/simulate/simulator-agents/${id}/delete/`,
  },
  agentDefinitions: {
    list: "/simulate/agent-definitions/",
    create: "/simulate/agent-definitions/create/",
    versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
    versionDetail: (id, version) =>
      `/simulate/agent-definitions/${id}/versions/${version}/`,
    createVersion: (id) => `/simulate/agent-definitions/${id}/versions/create/`,
    getCallLogs: (id, version) =>
      `/simulate/agent-definitions/${id}/versions/${version}/call-executions/`,
    detail: (id) => `/simulate/agent-definitions/${id}/`,
    delete: `/simulate/agent-definitions/`,
    getTestAnalytics: (agent, version) =>
      `/simulate/agent-definitions/${agent}/versions/${version}/eval-summary/`,
    verifyApiKey: `/tracer/observability-provider/verify_api_key/`,
    verifyAssistantId: `/tracer/observability-provider/verify_assistant_id/`,
    fetchAssistantFromProvider: `/simulate/api/agent-definition-operations/fetch_assistant_from_provider/`,
  },
  persona: {
    list: "/simulate/api/personas/",
    create: "/simulate/api/personas/",
    update: (id) => `/simulate/api/personas/${id}/`,
    delete: (id) => `/simulate/api/personas/${id}/`,
    duplicate: (id) => `/simulate/api/personas/duplicate/${id}/`,
  },
  runTests: {
    list: "/simulate/run-tests/",
    create: "/simulate/run-tests/create/",
    detail: (id) => `/simulate/run-tests/${id}/`,
    detailExecutions: (id) => `/simulate/run-tests/${id}/executions/`,
    detailScenarios: (id) => `/simulate/run-tests/${id}/scenarios/`,
    runTest: (id) => `/simulate/run-tests/${id}/execute/`,
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
    createOptimization: `/simulate/api/agent-prompt-optimiser/`,
    getOptimizationDetails: (id) =>
      `/simulate/api/agent-prompt-optimiser/${id}`,
    getOptimizationSteps: (id) =>
      `/simulate/api/agent-prompt-optimiser/${id}/steps/`,
    getOptimizationGraph: (id) =>
      `/simulate/api/agent-prompt-optimiser/${id}/graph/`,
    getTrailPrompts: (id, trialId) =>
      `/simulate/api/agent-prompt-optimiser/${id}/trial/${trialId}/prompt/`,
    getTrialItems: (id, trialId) =>
      `/simulate/api/agent-prompt-optimiser/${id}/trial/${trialId}/scenarios/`,
    getOptimizationRuns: () => `/simulate/api/agent-prompt-optimiser/`,
  },
  workspaces: {
    list: "/accounts/workspace/list/",
    create: "/accounts/workspaces/",
    switch: "/accounts/workspace/switch/",
  },
  dashboard: {
    list: "/tracer/dashboard/",
    create: "/tracer/dashboard/",
    detail: (id) => `/tracer/dashboard/${id}/`,
    update: (id) => `/tracer/dashboard/${id}/`,
    delete: (id) => `/tracer/dashboard/${id}/`,
    query: "/tracer/dashboard/query/",
    metrics: "/tracer/dashboard/metrics/",
    filterValues: "/tracer/dashboard/filter_values/",
    simulationAgents: "/tracer/dashboard/simulation-agents/",
    widgets: (dashboardId) => `/tracer/dashboard/${dashboardId}/widgets/`,
    widgetDetail: (dashboardId, widgetId) =>
      `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/`,
    widgetQuery: (dashboardId, widgetId) =>
      `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/query/`,
    widgetPreview: (dashboardId) =>
      `/tracer/dashboard/${dashboardId}/widgets/preview/`,
    widgetReorder: (dashboardId) =>
      `/tracer/dashboard/${dashboardId}/widgets/reorder/`,
    widgetDuplicate: (dashboardId, widgetId) =>
      `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/duplicate/`,
  },
  savedViews: {
    list: "/tracer/saved-views/",
    create: "/tracer/saved-views/",
    detail: (id) => `/tracer/saved-views/${id}/`,
    update: (id) => `/tracer/saved-views/${id}/`,
    delete: (id) => `/tracer/saved-views/${id}/`,
    duplicate: (id) => `/tracer/saved-views/${id}/duplicate/`,
    reorder: "/tracer/saved-views/reorder/",
  },
  sharedLinks: {
    list: "/tracer/shared-links/",
    create: "/tracer/shared-links/",
    detail: (id) => `/tracer/shared-links/${id}/`,
    update: (id) => `/tracer/shared-links/${id}/`,
    delete: (id) => `/tracer/shared-links/${id}/`,
    addAccess: (id) => `/tracer/shared-links/${id}/access/`,
    removeAccess: (id, accessId) =>
      `/tracer/shared-links/${id}/access/${accessId}/`,
    resolve: (token) => `/tracer/shared/${token}/`,
  },
  organizations: {
    list: "/accounts/organizations/",
    switch: "/accounts/organizations/switch/",
    current: "/accounts/organizations/current/",
    create: "/accounts/organizations/new/",
    update: "/accounts/organizations/update/",
  },
  feed: {
    getFeed: `/tracer/trace-error-analysis/clusters/feed/`,
    getFeedDetails: (id) => `/tracer/trace-error-analysis/clusters/${id}/`,
  },
  errorFeed: {
    list: `/tracer/feed/issues/`,
    stats: `/tracer/feed/issues/stats/`,
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
    linearTeams: `/tracer/feed/integrations/linear/teams/`,
  },
  promptSimulation: {
    scenarios: "/simulate/prompt-simulations/scenarios/",
    simulations: (promptTemplateId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/`,
    detail: (promptTemplateId, runTestId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/${runTestId}/`,
    execute: (promptTemplateId, runTestId) =>
      `/simulate/prompt-templates/${promptTemplateId}/simulations/${runTestId}/execute/`,
  },
  gateway: {
    list: "/agentcc/gateways/",
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
    protectTemplates: "/agentcc/gateways/protect-templates/",
    setBudget: (id) => `/agentcc/gateways/${id}/set-budget/`,
    removeBudget: (id) => `/agentcc/gateways/${id}/remove-budget/`,
    mcpStatus: (id) => `/agentcc/gateways/${id}/mcp-status/`,
    mcpTools: (id) => `/agentcc/gateways/${id}/mcp-tools/`,
    updateMcpServer: (id) => `/agentcc/gateways/${id}/update-mcp-server/`,
    removeMcpServer: (id) => `/agentcc/gateways/${id}/remove-mcp-server/`,
    updateMcpGuardrails: (id) => `/agentcc/gateways/${id}/update-mcp-guardrails/`,
    testMcpTool: (id) => `/agentcc/gateways/${id}/test-mcp-tool/`,
    mcpResources: (id) => `/agentcc/gateways/${id}/mcp-resources/`,
    mcpPrompts: (id) => `/agentcc/gateways/${id}/mcp-prompts/`,
    apiKeys: "/agentcc/api-keys/",
    createApiKey: "/agentcc/api-keys/",
    apiKeyDetail: (id) => `/agentcc/api-keys/${id}/`,
    updateApiKey: (id) => `/agentcc/api-keys/${id}/`,
    revokeApiKey: (id) => `/agentcc/api-keys/${id}/revoke/`,
    syncApiKeys: "/agentcc/api-keys/sync/",
    requestLogs: "/agentcc/request-logs/",
    requestLogDetail: (id) => `/agentcc/request-logs/${id}/`,
    requestLogSearch: "/agentcc/request-logs/search/",
    requestLogSessions: "/agentcc/request-logs/sessions/",
    requestLogSessionDetail: (sessionId) =>
      `/agentcc/request-logs/sessions/${sessionId}/`,
    requestLogExport: "/agentcc/request-logs/export/",
    analyticsOverview: "/agentcc/analytics/overview/",
    analyticsUsage: "/agentcc/analytics/usage-timeseries/",
    analyticsCost: "/agentcc/analytics/cost-breakdown/",
    analyticsLatency: "/agentcc/analytics/latency-stats/",
    analyticsErrors: "/agentcc/analytics/error-breakdown/",
    analyticsModels: "/agentcc/analytics/model-comparison/",
    orgConfig: {
      list: "/agentcc/org-configs/",
      active: "/agentcc/org-configs/active/",
      create: "/agentcc/org-configs/",
      detail: (cfgId) => `/agentcc/org-configs/${cfgId}/`,
      activate: (cfgId) => `/agentcc/org-configs/${cfgId}/activate/`,
      diff: (cfgId) => `/agentcc/org-configs/${cfgId}/diff/`,
    },
    webhooks: {
      list: "/agentcc/webhooks/",
      create: "/agentcc/webhooks/",
      detail: (id) => `/agentcc/webhooks/${id}/`,
      update: (id) => `/agentcc/webhooks/${id}/`,
      delete: (id) => `/agentcc/webhooks/${id}/`,
      test: (id) => `/agentcc/webhooks/${id}/test/`,
    },
    webhookEvents: {
      list: "/agentcc/webhook-events/",
      detail: (id) => `/agentcc/webhook-events/${id}/`,
      retry: (id) => `/agentcc/webhook-events/${id}/retry/`,
    },
    guardrailFeedback: {
      list: "/agentcc/guardrail-feedback/",
      create: "/agentcc/guardrail-feedback/",
      detail: (id) => `/agentcc/guardrail-feedback/${id}/`,
      summary: "/agentcc/guardrail-feedback/summary/",
    },
    guardrailAnalytics: {
      overview: "/agentcc/analytics/guardrail-overview/",
      rules: "/agentcc/analytics/guardrail-rules/",
      trends: "/agentcc/analytics/guardrail-trends/",
    },
    sessions: {
      list: "/agentcc/sessions/",
      create: "/agentcc/sessions/",
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
      list: "/agentcc/custom-properties/",
      create: "/agentcc/custom-properties/",
      detail: (id) => `/agentcc/custom-properties/${id}/`,
      update: (id) => `/agentcc/custom-properties/${id}/`,
      delete: (id) => `/agentcc/custom-properties/${id}/`,
      validate: "/agentcc/custom-properties/validate/",
    },
    emailAlerts: {
      list: "/agentcc/email-alerts/",
      create: "/agentcc/email-alerts/",
      detail: (id) => `/agentcc/email-alerts/${id}/`,
      update: (id) => `/agentcc/email-alerts/${id}/`,
      delete: (id) => `/agentcc/email-alerts/${id}/`,
      test: (id) => `/agentcc/email-alerts/${id}/test/`,
    },
    shadowExperiments: {
      list: "/agentcc/shadow-experiments/",
      create: "/agentcc/shadow-experiments/",
      detail: (id) => `/agentcc/shadow-experiments/${id}/`,
      update: (id) => `/agentcc/shadow-experiments/${id}/`,
      delete: (id) => `/agentcc/shadow-experiments/${id}/`,
      pause: (id) => `/agentcc/shadow-experiments/${id}/pause/`,
      resume: (id) => `/agentcc/shadow-experiments/${id}/resume/`,
      complete: (id) => `/agentcc/shadow-experiments/${id}/complete/`,
      stats: (id) => `/agentcc/shadow-experiments/${id}/stats/`,
    },
    shadowResults: {
      list: "/agentcc/shadow-results/",
      detail: (id) => `/agentcc/shadow-results/${id}/`,
    },
    providerCredentials: {
      fetchModels: "/agentcc/provider-credentials/fetch_models/",
    },
  },
  integrations: {
    connections: {
      list: "/integrations/connections/",
      create: "/integrations/connections/",
      detail: (id) => `/integrations/connections/${id}/`,
      update: (id) => `/integrations/connections/${id}/`,
      delete: (id) => `/integrations/connections/${id}/`,
      syncNow: (id) => `/integrations/connections/${id}/sync_now/`,
      pause: (id) => `/integrations/connections/${id}/pause/`,
      resume: (id) => `/integrations/connections/${id}/resume/`,
    },
    validate: "/integrations/connections/validate/",
    syncLogs: "/integrations/sync-logs/",
  },
  agentPlayground: {
    listGraphs: "/agent-playground/graphs/",
    createGraph: "/agent-playground/graphs/",
    createGraphFromTrace: "/agent-playground/graphs/from-trace/",
    deleteGraphs: "/agent-playground/graphs/delete/",
    graphDetail: (id) => `/agent-playground/graphs/${id}/`,
    updateGraph: (id) => `/agent-playground/graphs/${id}/`,
    graphVersions: (id) => `/agent-playground/graphs/${id}/versions/`,
    nodeTemplates: "/agent-playground/node-templates/",
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
    testNodeExecution: (graphId, versionId, nodeId) =>
      `/agent-playground/graphs/${graphId}/versions/${versionId}/nodes/${nodeId}/test-execution/`,
  },
  mcp: {
    config: "/mcp/config/",
    toolGroups: "/mcp/config/tool-groups/",
    sessions: "/mcp/sessions/",
    tools: "/mcp/internal/tools/",
    oauth: {
      authorize: "/mcp/oauth/authorize/",
      consent: "/mcp/oauth/consent/",
      approveInfo: "/mcp/oauth/approve-info/",
      approve: "/mcp/oauth/approve/",
    },
  },
  twoFactor: {
    status: "/accounts/2fa/status/",
    totp: {
      setup: "/accounts/2fa/totp/setup/",
      confirm: "/accounts/2fa/totp/confirm/",
      disable: "/accounts/2fa/totp/",
    },
    verify: {
      totp: "/accounts/2fa/verify/totp/",
      recovery: "/accounts/2fa/verify/recovery/",
      passkeyOptions: "/accounts/2fa/verify/passkey/options/",
      passkey: "/accounts/2fa/verify/passkey/",
    },
    recoveryCodes: {
      count: "/accounts/2fa/recovery-codes/",
      regenerate: "/accounts/2fa/recovery-codes/regenerate/",
    },
  },
  passkey: {
    list: "/accounts/passkeys/",
    registerOptions: "/accounts/passkey/register/options/",
    registerVerify: "/accounts/passkey/register/verify/",
    detail: (id) => `/accounts/passkeys/${id}/`,
    authenticateOptions: "/accounts/passkey/authenticate/options/",
    authenticateVerify: "/accounts/passkey/authenticate/verify/",
  },
  orgPolicy: {
    twoFactor: "/accounts/organization/2fa-policy/",
  },
  falconAI: {
    conversations: "/falcon-ai/conversations/",
    conversation: (id) => `/falcon-ai/conversations/${id}/`,
    messages: (id) => `/falcon-ai/conversations/${id}/messages/`,
    feedback: (id) => `/falcon-ai/messages/${id}/feedback/`,
    connectors: "/falcon-ai/mcp-connectors/",
    connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
    connectorDiscover: (id) => `/falcon-ai/mcp-connectors/${id}/discover/`,
    connectorTest: (id) => `/falcon-ai/mcp-connectors/${id}/test/`,
    connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
    connectorAuth: (id) => `/falcon-ai/mcp-connectors/${id}/authenticate/`,
    skills: "/falcon-ai/skills/",
    skill: (id) => `/falcon-ai/skills/${id}/`,
    fileUpload: "/falcon-ai/files/upload/",
    quickAnalysis: "/falcon-ai/quick-analysis/",
  },
  imagineAnalysis: {
    trigger: "/tracer/imagine-analysis/",
    poll: "/tracer/imagine-analysis/",
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
