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
import { apiPath } from "src/api/contracts/api-surface";
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

axiosInstance.interceptors.request.use((config) =>
  assertContractedRequestConfig(config),
);

axiosInstance.interceptors.response.use(
  (res) => {
    return assertContractedResponse(res);
  },
  async (error) => {
    if (error?.response) {
      try {
        error.response = assertContractedResponse(error.response);
      } catch (contractError) {
        return Promise.reject(contractError);
      }
    }

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

const withQuery = (path, params) => {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      searchParams.set(key, String(value));
    }
  });
  const queryString = searchParams.toString();
  return queryString ? `${path}?${queryString}` : path;
};

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
    service: (provider) =>
      withQuery(apiPath("/saml2_auth/login/"), { provider }),
    create_org: apiPath("/accounts/team/users/"),
    ssoLogin: (email) =>
      withQuery(apiPath("/saml2_auth/idp-login/"), { email }),
    logout: apiPath("/accounts/logout/"),
    refreshToken: apiPath("/accounts/token/refresh/"),
    awsSignUp: apiPath("/accounts/aws-marketplace/signup/"),
    config: apiPath("/accounts/config/"),
    createOrganization: apiPath("/accounts/organizations/create/"),
  },
  workspace: {
    getMembers: (workspace_id) =>
      apiPath("/accounts/workspaces/{workspace_id}/members/", {
        workspace_id: workspace_id,
      }),
    userList: apiPath("/accounts/user/list/"),
    workspaceList: apiPath("/accounts/workspace/list/"),
    updateRole: apiPath("/accounts/user/role/update/"),
    resendInvite: apiPath("/accounts/user/resend-invite/"),
    deleteUser: apiPath("/accounts/user/delete/"),
    workspaceInvite: apiPath("/accounts/workspace/invite/"),
    deactivate: apiPath("/accounts/user/deactivate/"),
    removeUserFromWrokspace: (workspace_id, member_id) =>
      apiPath("/accounts/workspaces/{workspace_id}/members/{member_id}/", {
        workspace_id: workspace_id,
        member_id: member_id,
      }),
    workspaceUpdate: (workspace_id) =>
      apiPath("/accounts/workspaces/{workspace_id}/", {
        workspace_id: workspace_id,
      }),
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
    workspaceMemberList: (wsId) =>
      apiPath("/accounts/workspace/{workspace_id}/members/", {
        workspace_id: wsId,
      }),
    workspaceMemberRoleUpdate: (wsId) =>
      apiPath("/accounts/workspace/{workspace_id}/members/role/", {
        workspace_id: wsId,
      }),
    workspaceMemberRemove: (wsId) =>
      apiPath("/accounts/workspace/{workspace_id}/members/remove/", {
        workspace_id: wsId,
      }),
  },
  invite: {
    accept_invitation: (uidb64, token) =>
      apiPath("/accounts/accept-invitation/{uidb64}/{token}/", {
        uidb64,
        token,
      }),
  },
  model: {
    list: apiPath("/model-hub/custom-models/"),
    details: (id) => apiPath("/model-hub/custom-models/{id}/", { id }),
    updateMetric: (id) =>
      apiPath("/model-hub/custom_models/update-metric/{id}/", { id }),
    performance: (id) => apiPath("/model-hub/performance/{id}/", { id }),
    create: apiPath("/model-hub/custom_models/create/"),
    updateDefaultDataset: (id) =>
      apiPath("/model-hub/custom_models/update-baseline/{id}/", { id }),
    modelList: apiPath("/model-hub/custom-models/list/"),
    deleteModel: () => apiPath("/model-hub/custom_models/delete/"),
    getModelDetail: (id) => apiPath("/model-hub/custom-models/{id}/", { id }),
  },
  dataset: {
    options: (id) =>
      apiPath("/model-hub/performance/options/{model_id}/", { model_id: id }),
    promptSummary: (id) =>
      apiPath("/model-hub/dataset/{dataset_id}/run-prompt-stats/", {
        dataset_id: id,
      }),
    evalsSummary: (id) =>
      apiPath("/model-hub/dataset/{dataset_id}/eval-stats/", {
        dataset_id: id,
      }),
    annotationSummary: (id) =>
      apiPath("/model-hub/dataset/{dataset_id}/annotation-summary/", {
        dataset_id: id,
      }),
    baseColumndata: apiPath("/model-hub/datasets/get-base-columns/"),
    criticalIssue: (id) =>
      apiPath("/model-hub/datasets/explanation-summary/{dataset_id}/", {
        dataset_id: id,
      }),
    criticalIssueRefresh: (id) =>
      apiPath("/model-hub/datasets/explanation-summary/{dataset_id}/refresh/", {
        dataset_id: id,
      }),
    getCompareDataset: (id) =>
      apiPath("/model-hub/datasets/{dataset_id}/compare-datasets/", {
        dataset_id: id,
      }),
    getCompareDatasetDownload: (id) =>
      apiPath("/model-hub/datasets/{dataset_id}/compare-datasets/download/", {
        dataset_id: id,
      }),
    getSummaryTable: (id) =>
      apiPath("/model-hub/datasets/{dataset_id}/compare-stats/", {
        dataset_id: id,
      }),
    getCompareDatasetRow: (compareId, rowId) =>
      apiPath("/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/", {
        compare_id: compareId,
        row_id: rowId,
      }),
    deleteCompareDataset: (compareId) =>
      apiPath("/model-hub/datasets/delete-compare/{compare_id}/", {
        compare_id: compareId,
      }),
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
    annotationsUser: (id) =>
      apiPath("/model-hub/organizations/{organization_id}/users/", {
        organization_id: id,
      }),
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
    list: (modelId) =>
      apiPath("/model-hub/custom-metric/{model_id}/", {
        model_id: modelId,
      }),
    create: apiPath("/model-hub/custom-metric/create/"),
    edit: apiPath("/model-hub/custom-metric/update/"),
    all: (modelId) =>
      apiPath("/model-hub/custom-metric/all/{model_id}/", {
        model_id: modelId,
      }),
    tagOptions: (metricId) =>
      apiPath("/model-hub/custom-metric/tag-options/{metric_id}/", {
        metric_id: metricId,
      }),
    testMetric: apiPath("/model-hub/custom-metric/test/"),
  },
  performance: {
    graphData: (id) => apiPath("/model-hub/performance/{id}/", { id }),
    tableData: (id) => apiPath("/model-hub/performance/detail/{id}/", { id }),
    tableExport: (id) => apiPath("/model-hub/performance/export/{id}/", { id }),
    getFilterOptions: (modelId) =>
      apiPath("/model-hub/performance/options/{model_id}/", {
        model_id: modelId,
      }),
    getTagDistribution: (modelId) =>
      apiPath("/model-hub/performance/tag-distribution/{model_id}/", {
        model_id: modelId,
      }),
  },
  performanceReport: {
    create: (modelId) =>
      apiPath("/model-hub/performance/report/{model_id}/", {
        model_id: modelId,
      }),
    list: (modelId) =>
      apiPath("/model-hub/performance/report/{model_id}/", {
        model_id: modelId,
      }),
    delete: (modelId, reportId) =>
      apiPath("/model-hub/performance/report/{model_id}/{report_id}/", {
        model_id: modelId,
        report_id: reportId,
      }),
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
    stopOptimization: (id) =>
      apiPath("/model-hub/dataset-optimization/{id}/stop/", { id: id }),
    getAll: apiPath("/model-hub/optimize-dataset/"),
    getColumns: (id) =>
      apiPath("/model-hub/optimize-dataset/{model_id}/column-config/", {
        model_id: id,
      }),
    updateColumns: (id) =>
      apiPath("/model-hub/optimize-dataset/{model_id}/column-config/", {
        model_id: id,
      }),
    getOptimizeRightAnswer: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/right-answers/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    getRightAnsColumns: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    updateRightAnsColumns: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/column-config/right-answers/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    getPromptTemplateExplore: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/prompt-template-explore/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    getPromptTemplateExploreColumns: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    updatePromptTemplateExploreColumns: (model_id, optimization_id) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/column-config/prompt-template-explore/{optimization_id}/",
        { model_id: model_id, optimization_id: optimization_id },
      ),
    getPromptTemplateResults: (modelId, optimizationId) =>
      apiPath(
        "/model-hub/optimize-dataset/{model_id}/prompt-template-result/{optimization_id}/",
        { model_id: modelId, optimization_id: optimizationId },
      ),
    getOptimizationDetail: (modelId, optimizationId) =>
      apiPath("/model-hub/optimize-dataset/{model_id}/{optimization_id}/", {
        model_id: modelId,
        optimization_id: optimizationId,
      }),
  },
  settings: {
    teams: {
      getMemberList: apiPath("/accounts/team/users/"),
      deleteMember: (id) =>
        apiPath("/accounts/team/users/{member_id}/", { member_id: id }),
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
    usageTotals: apiPath("/usage/workspace-usage-summary/"),
    workspaceUsage: apiPath("/usage/workspace-eval-summary/"),
    usageMetrics: apiPath("/usage/usage-summary/"),
    v2: {
      usageOverview: apiPath("/usage/v2/usage-overview/"),
      usageTimeSeries: apiPath("/usage/v2/usage-time-series/"),
      usageWorkspaceBreakdown: apiPath("/usage/v2/usage-workspace-breakdown/"),
      plansAndAddons: apiPath("/usage/v2/plans-and-addons/"),
      billingOverview: apiPath("/usage/v2/billing-overview/"),
      invoices: apiPath("/usage/v2/invoices/"),
      invoiceDetail: (id) =>
        apiPath("/usage/v2/invoices/{invoice_id}/", { invoice_id: id }),
      notifications: apiPath("/usage/v2/notifications/"),
      budgets: apiPath("/usage/v2/budgets/"),
      budgetDetail: (id) =>
        apiPath("/usage/v2/budgets/{budget_id}/", { budget_id: id }),
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
        apiPath("/usage/v2/payment-methods/{pm_id}/default/", { pm_id: pmId }),
      paymentMethodDelete: (pmId) =>
        apiPath("/usage/v2/payment-methods/{pm_id}/", { pm_id: pmId }),
      deploymentInfo: apiPath("/api/deployment-info/"),
    },
  },
  tools: {
    create: apiPath("/model-hub/tools/"),
    update: (id) => apiPath("/model-hub/tools/{id}/", { id: id }),
  },
  secrets: {
    list: apiPath("/model-hub/secrets/"),
    create: apiPath("/model-hub/secrets/"),
  },
  huggingFace: {
    list: apiPath("/model-hub/datasets/huggingface/list/"),
    detail: apiPath("/model-hub/datasets/huggingface/detail/"),
    addHuggingFaceRow: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_rows_from_huggingface/", {
        dataset_id: datasetId,
      }),
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
      apiPath("/model-hub/dataset/columns/{dataset_id}/", {
        dataset_id: datasetId,
      }),
    getJsonColumnSchema: (datasetId) =>
      apiPath("/model-hub/dataset/{dataset_id}/json-schema/", {
        dataset_id: datasetId,
      }),
    getDatasetDetail: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
        dataset_id: datasetId,
      }),
    updateCellValue: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
        dataset_id: datasetId,
      }),
    downloadDataset: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/download_dataset/", {
        dataset_id: datasetId,
      }),
    updateDataset: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/edit_dataset_behavior/", {
        dataset_id: datasetId,
      }),
    uploadDatasetLocalFile: apiPath(
      "/model-hub/develops/create-dataset-from-local-file/",
    ),
    uploadDatasetRow: apiPath("/model-hub/develops/add_rows_from_file/"),
    addEmptyRow: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_empty_rows/", {
        dataset_id: datasetId,
      }),
    getSyntheticConfig: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/synthetic-config/", {
        dataset_id: datasetId,
      }),
    createSyntheticDataset: apiPath(
      "/model-hub/develops/create-synthetic-dataset/",
    ),
    updateSyntheticDataset: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/update-synthetic-config/", {
        dataset_id: datasetId,
      }),
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
      apiPath("/model-hub/develops/clone-dataset/{dataset_id}/", {
        dataset_id: newDatasetId,
      }),
    createFromExistingDataset: apiPath("/model-hub/develops/add-as-new/"),
    addAsNewDataset: (datasetId) =>
      apiPath("/model-hub/develops/{exp_dataset_id}/create-dataset/", {
        exp_dataset_id: datasetId,
      }),
    individualExperimentDataset: (datasetId) =>
      apiPath(
        "/model-hub/develops/{experiment_dataset_id}/get-experiment-dataset-table/",
        { experiment_dataset_id: datasetId },
      ),
    addColumn: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
        dataset_id: datasetId,
      }),
    addMultipleColumns: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_multiple_static_columns/", {
        dataset_id: datasetId,
      }),
    updateColumnName: (datasetId, columnId) =>
      apiPath(
        "/model-hub/develops/{dataset_id}/update_column_name/{column_id}/",
        { dataset_id: datasetId, column_id: columnId },
      ),
    updateColumnType: (datasetId, columnId) =>
      apiPath(
        "/model-hub/develops/{dataset_id}/update_column_type/{column_id}/",
        { dataset_id: datasetId, column_id: columnId },
      ),
    deleteColumn: (datasetId, columnId) =>
      apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
        dataset_id: datasetId,
        column_id: columnId,
      }),
    deleteDataset: () => apiPath("/model-hub/develops/delete_dataset/"),
    addDatasetColumn: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/add_columns/", {
        dataset_id: datasetId,
      }),
    addRowFromExistingDataset: (datasetId) =>
      apiPath(
        "/model-hub/develops/{dataset_id}/add_rows_from_existing_dataset/",
        { dataset_id: datasetId },
      ),
    getRowData: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/get-row-data/", {
        dataset_id: datasetId,
      }),
    addColumns: {
      apiCall: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/add-api-column/", {
          dataset_id: datasetId,
        }),
      extractEntities: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/extract-entities/", {
          dataset_id: datasetId,
        }),
      classifyColumn: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/classify-column/", {
          dataset_id: datasetId,
        }),
      extractJsonKey: (datasetId) =>
        apiPath("/model-hub/develops/{dataset_id}/extract-json-column/", {
          dataset_id: datasetId,
        }),
      addVectorDBColumn: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/add_vector_db_column/", {
          dataset_id: datasetId,
        }),
      preview: (datasetId, operationType) =>
        apiPath("/model-hub/datasets/{dataset_id}/preview/{operation_type}/", {
          dataset_id: datasetId,
          operation_type: operationType,
        }),
      conditionalnode: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/conditional-column/", {
          dataset_id: datasetId,
        }),
      getColumnConfig: (columnId) =>
        apiPath("/model-hub/columns/{column_id}/operation-config/", {
          column_id: columnId,
        }),
      updateDynamicColumn: (columnId) =>
        apiPath("/model-hub/columns/{column_id}/rerun-operation/", {
          column_id: columnId,
        }),
    },
    deleteDatasetRow: (datasetId) =>
      apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
        dataset_id: datasetId,
      }),
    duplicateDatasetRows: (datasetId) =>
      apiPath("/model-hub/datasets/{dataset_id}/duplicate-rows/", {
        dataset_id: datasetId,
      }),
    createDatasetRows: (datasetId) =>
      apiPath("/model-hub/datasets/{dataset_id}/duplicate/", {
        dataset_id: datasetId,
      }),
    mergeDatasetRows: (datasetId) =>
      apiPath("/model-hub/datasets/{dataset_id}/merge/", {
        dataset_id: datasetId,
      }),
    evaluateRows: () => apiPath("/model-hub/evaluate-rows/"),
    evaluateRunRows: () => apiPath("/model-hub/run-prompt-for-rows/"),
    eval: {
      createCustomEval: apiPath("/model-hub/create_custom_evals/"),
      getEvalsList: (datasetId) =>
        apiPath("/model-hub/develops/{dataset_id}/get_evals_list/", {
          dataset_id: datasetId,
        }),
      getCompareEvalsList: () =>
        apiPath("/model-hub/datasets/compare/get-evals-list/"),
      getPreviouslyConfiguredEvalTemplateConfig: (datasetId, templateId) =>
        apiPath(
          "/model-hub/develops/{dataset_id}/get_eval_structure/{eval_id}/",
          { dataset_id: datasetId, eval_id: templateId },
        ),
      addEval: (datasetId) =>
        apiPath("/model-hub/develops/{dataset_id}/add_user_eval/", {
          dataset_id: datasetId,
        }),
      addCompareEval: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/", {
          dataset_id: datasetId,
        }),
      runEvals: (datasetId) =>
        apiPath("/model-hub/develops/{dataset_id}/start_evals_process/", {
          dataset_id: datasetId,
        }),
      compareRunEvals: (datasetId) =>
        apiPath(
          "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/",
          { dataset_id: datasetId },
        ),
      deleteEval: (datasetId, evalId) =>
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          { dataset_id: datasetId, eval_id: evalId },
        ),
      editEval: (datasetId, evalId) =>
        apiPath(
          "/model-hub/develops/{dataset_id}/edit_and_run_user_eval/{eval_id}/",
          { dataset_id: datasetId, eval_id: evalId },
        ),
      stopEval: (datasetId, evalId) =>
        apiPath("/model-hub/develops/{dataset_id}/stop_user_eval/{eval_id}/", {
          dataset_id: datasetId,
          eval_id: evalId,
        }),
      testEval: (datasetId) =>
        apiPath("/model-hub/develops/{dataset_id}/preview_run_eval/", {
          dataset_id: datasetId,
        }),
      getFunctionEvalsList: apiPath("/model-hub/develops/get_function_list/"),
      addFeedback: apiPath("/model-hub/feedback/"),
      getFeedbackTemplate: apiPath("/model-hub/feedback/get_template/"),
      getFeedbackTemplateTrace: (id) =>
        apiPath("/tracer/custom-eval-config/{id}/", { id }),
      updateFeedback: apiPath("/model-hub/feedback/submit-feedback/"),
      getFeedbackDetails: apiPath("/model-hub/feedback/get-feedback-details/"),
      getEvalLogs: apiPath("/model-hub/get-eval-logs"),
      runCellErrorLocalizer: (cellId) =>
        apiPath("/model-hub/cells/{cell_id}/run-error-localizer/", {
          cell_id: cellId,
        }),
      getCellErrorLocalizer: (cellId) =>
        apiPath("/model-hub/cells/{cell_id}/run-error-localizer/", {
          cell_id: cellId,
        }),
      getEvalsLogs: apiPath("/model-hub/get-eval-logs-details"),
      getEvalMetrics: apiPath("/model-hub/get-eval-metrics"),
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
      getEvalVersions: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/versions/", {
          template_id: id,
        }),
      createEvalVersion: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/versions/create/", {
          template_id: id,
        }),
      setDefaultVersion: (templateId, versionId) =>
        apiPath(
          "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
          { template_id: templateId, version_id: versionId },
        ),
      restoreVersion: (templateId, versionId) =>
        apiPath(
          "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
          { template_id: templateId, version_id: versionId },
        ),
      getCompositeDetail: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/composite/", {
          template_id: id,
        }),
      executeCompositeEval: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/composite/execute/", {
          template_id: id,
        }),
      executeCompositeEvalAdhoc: apiPath(
        "/model-hub/eval-templates/composite/execute-adhoc/",
      ),
      getEvalDetail: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/detail/", {
          template_id: id,
        }),
      updateEvalTemplate: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/update/", {
          template_id: id,
        }),
      getEvalUsage: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/usage/", {
          template_id: id,
        }),
      getEvalFeedbackList: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/feedback-list/", {
          template_id: id,
        }),
      // Ground Truth (Phase 9)
      getGroundTruthList: (id) =>
        apiPath("/model-hub/eval-templates/{template_id}/ground-truth/", {
          template_id: id,
        }),
      uploadGroundTruth: (id) =>
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
          { template_id: id },
        ),
      groundTruthSetup: (id) =>
        apiPath("/model-hub/ground-truth/{ground_truth_id}/setup/", {
          ground_truth_id: id,
        }),
      groundTruthData: (id) =>
        apiPath("/model-hub/ground-truth/{ground_truth_id}/data/", {
          ground_truth_id: id,
        }),
      groundTruthStatus: (id) =>
        apiPath("/model-hub/ground-truth/{ground_truth_id}/status/", {
          ground_truth_id: id,
        }),
      groundTruthEmbed: (id) =>
        apiPath("/model-hub/ground-truth/{ground_truth_id}/embed/", {
          ground_truth_id: id,
        }),
      deleteGroundTruth: (id) =>
        apiPath("/model-hub/ground-truth/{ground_truth_id}/", {
          ground_truth_id: id,
        }),
      runEval: apiPath("/model-hub/test-evaluation/"),
      getEvalConfigs: apiPath("/model-hub/get-eval-config"),
      getEvalNames: apiPath("/model-hub/get-eval-template-names"),
      aiFilter: apiPath("/model-hub/ai-filter/"),
      aiEvalWriter: apiPath("/model-hub/ai-eval-writer/"),
      summaryTemplates: apiPath("/model-hub/eval-summary-templates/"),
      summaryTemplate: (id) =>
        apiPath("/model-hub/eval-summary-templates/{template_id}/", {
          template_id: id,
        }),
      evalPlayground: apiPath("/model-hub/eval-playground/"),
      updateEvalsTemplate: apiPath("/model-hub/update-eval-template/"),
      testEvaluation: apiPath("/model-hub/test-evaluation/"),
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
      getPrompt: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/", { id: id }),
      getNameChange: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/save-name/", { id: id }),
      generatePrompt: apiPath("/model-hub/prompt-templates/generate-prompt/"),
      generateVariables: apiPath(
        "/model-hub/prompt-templates/generate-variables/",
      ),
      getStatus: (/** @type {string} */ id) =>
        apiPath("/model-hub/prompt-templates/{id}/get-run-status/", { id: id }),
      getPromptVersions: () => apiPath("/model-hub/prompt-history-executions/"),
      // https://dev.api.futureagi.com/model-hub/prompt-templates/6b6b4d0b-ef4f-4a8b-82e9-2d2bbaedc6b5/run_template/
      runTemplatePrompt: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/run_template/", { id: id }),
      getRunPrompt: () =>
        apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
      editRunPrompt: () =>
        apiPath("/model-hub/develops/edit_run_prompt_column/"),
      applyVariables: () => apiPath("/model-hub/get-column-values/"),
      promptExecutions: () => apiPath("/model-hub/prompt-executions/"),
      promptDelete: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/", { id: id }),
      promptMultiDelete: apiPath("/model-hub/prompt-templates/bulk-delete/"),
      analyzePrompt: apiPath("/model-hub/prompt-templates/analyze-prompt/"),
      improvePrompt: apiPath("/model-hub/prompt-templates/improve-prompt/"),
      updatePrompt: apiPath("/model-hub/prompt-templates/improve-prompt/"),
      responseSchema: apiPath("/model-hub/response_schema/"),
      saveDefaultPrompt: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/set_default/", { id: id }),
      commitSavePrompt: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/commit/", { id: id }),
      getAllVariables: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/all-variables/", { id: id }),
      getDerivedVariables: (id) =>
        apiPath("/model-hub/prompt-templates/{prompt_id}/derived-variables/", {
          prompt_id: id,
        }),
      getDerivedVariableSchema: (id, columnName) =>
        apiPath(
          "/model-hub/prompt-templates/{prompt_id}/derived-variables/{column_name}/schema/",
          { prompt_id: id, column_name: columnName },
        ),
      extractDerivedVariables: (id) =>
        apiPath(
          "/model-hub/prompt-templates/{prompt_id}/derived-variables/extract/",
          { prompt_id: id },
        ),
      previewDerivedVariables: apiPath(
        "/model-hub/prompt-templates/derived-variables/preview/",
      ),
      getDatasetDerivedVariables: (datasetId) =>
        apiPath("/model-hub/datasets/{dataset_id}/derived-variables/", {
          dataset_id: datasetId,
        }),
      compareVersions: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/compare-versions/", {
          id: id,
        }),
      addDraftInPrompt: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/add-new-draft/", { id: id }),
      stopGenerating: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/stop-streaming/", { id: id }),
      getEvaluationData: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/evaluations/", { id: id }),
      getEvaluationConfigs: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/evaluation-configs/", {
          id: id,
        }),
      createOrUpdateEvalConfig: (id) =>
        apiPath("/model-hub/prompt-templates/{id}/update-evaluation-configs/", {
          id: id,
        }),
      deleteEvalConfig: (promptTemplate, evalId) =>
        withQuery(
          apiPath(
            "/model-hub/prompt-templates/{id}/delete-evaluation-config/",
            { id: promptTemplate },
          ),
          { id: evalId },
        ),
      runEvalsOnMultipleVersions: (id) =>
        apiPath(
          "/model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
          { id: id },
        ),
      promptLabels: apiPath("/model-hub/prompt-labels/"),
      createPromptLabel: apiPath("/model-hub/prompt-labels/"),
      deletePromptLabel: (id) =>
        apiPath("/model-hub/prompt-labels/{id}/", { id: id }),
      assignLabels: (promptId, labelId) =>
        apiPath(
          "/model-hub/prompt-labels/{template_id}/{label_id}/assign-label-by-id/",
          { template_id: promptId, label_id: labelId },
        ),
      assignMultipleLabels: apiPath(
        "/model-hub/prompt-labels/assign-multiple-labels/",
      ),
      removeLabel: () => apiPath("/model-hub/prompt-labels/remove/"),
      getPromptMetrics: () => apiPath("/model-hub/prompt/metrics/"),
      getPromptSpanMetrics: () => apiPath("/model-hub/prompt/span-metrics/"),
      promptMetricEmptyScreen: () =>
        apiPath("/model-hub/prompt/metrics/empty-screen"),
      promptFolder: apiPath("/model-hub/prompt-folders/"),
      promptFolderId: (id) =>
        apiPath("/model-hub/prompt-folders/{id}/", { id: id }),
      movePrompt: (folderId) =>
        apiPath("/model-hub/prompt-templates/{id}/save-prompt-folder/", {
          id: folderId,
        }),
      promptTemplate: apiPath("/model-hub/prompt-base-templates/"),
      promptTemplateId: (id) =>
        apiPath("/model-hub/prompt-base-templates/{id}/", { id: id }),
      categories: apiPath(
        "/model-hub/prompt-base-templates/get-all-categories/",
      ),
    },
    optimizeDevelop: {
      columnInfo: apiPath("/model-hub/metrics/by-column/"),
      create: apiPath("/model-hub/optimisation/create/"),
      list: apiPath("/model-hub/optimisation/"),
      detail: (optimizationId) =>
        apiPath("/model-hub/optimisation/{id}/details/", {
          id: optimizationId,
        }),
    },
    datasetOptimization: {
      create: apiPath("/model-hub/dataset-optimization/"),
      list: apiPath("/model-hub/dataset-optimization/"),
      detail: (id) =>
        apiPath("/model-hub/dataset-optimization/{id}/", { id: id }),
      steps: (id) =>
        apiPath("/model-hub/dataset-optimization/{id}/steps/", { id: id }),
      graph: (id) =>
        apiPath("/model-hub/dataset-optimization/{id}/graph/", { id: id }),
      trialPrompt: (id, trialId) =>
        apiPath(
          "/model-hub/dataset-optimization/{id}/trial/{trial_id}/prompt/",
          { id: id, trial_id: trialId },
        ),
      trialDetail: (id, trialId) =>
        apiPath("/model-hub/dataset-optimization/{id}/trial/{trial_id}/", {
          id: id,
          trial_id: trialId,
        }),
      trialScenarios: (id, trialId) =>
        apiPath(
          "/model-hub/dataset-optimization/{id}/trial/{trial_id}/scenarios/",
          { id: id, trial_id: trialId },
        ),
      trialEvaluations: (id, trialId) =>
        apiPath(
          "/model-hub/dataset-optimization/{id}/trial/{trial_id}/evaluations/",
          { id: id, trial_id: trialId },
        ),
    },
    experiment: {
      index: apiPath("/model-hub/experiments/v2/"),
      update: (id) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/", {
          experiment_id: id,
        }),
      create: () => apiPath("/model-hub/experiments/v2/"),
      getExperimentDetails: (id) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/", {
          experiment_id: id,
        }),
      experimentListPaginated: apiPath("/model-hub/experiments/v2/list/"),
      experimentList: apiPath("/model-hub/experiment-detail/"),
      experimentDetail: (experimentId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/rows/", {
          experiment_id: experimentId,
        }),
      downloadExperiment: (experimentId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/download/", {
          experiment_id: experimentId,
        }),
      runEvaluation: (experimentId) =>
        apiPath("/model-hub/experiments/{experiment_id}/run-evaluations/", {
          experiment_id: experimentId,
        }),
      addEval: (experimentId) =>
        apiPath("/model-hub/experiments/{experiment_id}/add-eval/", {
          experiment_id: experimentId,
        }),
      getSummary: (experimentId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/stats/", {
          experiment_id: experimentId,
        }),
      compareExperiments: (experimentId) =>
        apiPath(
          "/model-hub/experiments/v2/{experiment_id}/compare-experiments/",
          { experiment_id: experimentId },
        ),
      comparison: (experimentId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/comparisons/", {
          experiment_id: experimentId,
        }),
      // deleteExperiment: () => `/model-hub/experiments/delete/`,
      rerun: apiPath("/model-hub/experiments/v2/re-run/"),
      delete: apiPath("/model-hub/experiments/v2/delete/"),
      stop: (id) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/stop/", {
          experiment_id: id,
        }),
      rowDetail: (experimentId, rowId) =>
        apiPath("/model-hub/experiments/{experiment_id}/{row_id}/", {
          experiment_id: experimentId,
          row_id: rowId,
        }),
      reRunExperimentCell: (experimentId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/rerun-cells/", {
          experiment_id: experimentId,
        }),
      suggestName: (datasetId) =>
        apiPath("/model-hub/experiments/v2/suggest-name/{dataset_id}/", {
          dataset_id: datasetId,
        }),
      validateName: apiPath("/model-hub/experiments/v2/validate-name/"),
      getExperimentJSONSchema: (expId) =>
        apiPath("/model-hub/experiments/v2/{experiment_id}/json-schema/", {
          experiment_id: expId,
        }),
      getExperimentDerivedVariables: (expId) =>
        apiPath(
          "/model-hub/experiments/v2/{experiment_id}/derived-variables/",
          { experiment_id: expId },
        ),
      feedback: {
        getTemplate: (experimentId) =>
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/",
            { experiment_id: experimentId },
          ),
        create: (experimentId) =>
          apiPath("/model-hub/experiments/v2/{experiment_id}/feedback/", {
            experiment_id: experimentId,
          }),
        getDetails: (experimentId) =>
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
            { experiment_id: experimentId },
          ),
        submit: (experimentId) =>
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
            { experiment_id: experimentId },
          ),
      },
    },
    apiKey: {
      create: apiPath("/model-hub/api-keys/"),
      status: apiPath("/model-hub/develops/provider-status/"),
      update: apiPath("/model-hub/api-keys/"),
      delete: (id) => apiPath("/model-hub/api-keys/{id}/", { id: id }),
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
    getProjectVersion: (runId) =>
      apiPath("/tracer/project-version/{id}/", { id: runId }),
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
    submitObservationSpanFeedbackActionType: () =>
      apiPath("/tracer/observation-span/submit_feedback_action_type/"),
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
    getTraceErrorAnalysis: (id) =>
      apiPath("/tracer/trace-error-analysis/{trace_id}/", { trace_id: id }),
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
    updateEvalTask: (id) => apiPath("/tracer/eval-task/{id}/", { id: id }),
    listEvalsWithProject: () =>
      apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    listProjects: () => apiPath("/tracer/project/list_project_ids/"),
    showCharts: () => apiPath("/tracer/project/get_graph_data/"),
    getMonitorList: () => apiPath("/tracer/user-alerts/list_monitors/"),
    duplicateMonitorList: () => apiPath("/tracer/user-alerts/duplicate/"),
    createMonitor: apiPath("/tracer/user-alerts/"),
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
      withQuery(apiPath("/tracer/eval-task/get_eval_details/"), {
        eval_id: id,
      }),
    patchEvalTask: () => apiPath("/tracer/eval-task/update_eval_task/"),
    getEvalTaskLogs: () => apiPath("/tracer/eval-task/get_eval_task_logs/"),
    getEvalTaskUsage: () => apiPath("/tracer/eval-task/get_usage/"),
    getSessionEvalLogs: (sessionId) =>
      apiPath("/tracer/trace-session/{id}/eval_logs/", { id: sessionId }),
    createEvalTaskConfig: () => apiPath("/tracer/custom-eval-config/"),
    updateEvalTaskConfig: (id) =>
      apiPath("/tracer/custom-eval-config/{id}/", { id: id }),
    getEvalTaskConfig: () =>
      apiPath("/tracer/custom-eval-config/list_custom_eval_configs/"),
    pauseEvalTask: (id) =>
      withQuery(apiPath("/tracer/eval-task/pause_eval_task/"), {
        eval_task_id: id,
      }),
    resumeEvalTask: (id) =>
      withQuery(apiPath("/tracer/eval-task/unpause_eval_task/"), {
        eval_task_id: id,
      }),
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
    getMonitorMetricOptions: apiPath("/tracer/user-alerts/metric-options/"),
    muteAlerts: apiPath("/tracer/user-alerts/bulk-mute/"),
    resolveAlerts: apiPath("/tracer/user-alert-logs/resolve/"),
    getAlertDetails: (alertId) =>
      apiPath("/tracer/user-alerts/{id}/details/", { id: alertId }),
    getAlertGraph: (alertId) =>
      apiPath("/tracer/user-alerts/{id}/graph/", { id: alertId }),
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
    getEvalConfigs: apiPath("/tracer/replay-session/eval-configs/"),
    replaySession: apiPath("/tracer/replay-session/"),
    generateReplayScenarios: (id) =>
      apiPath("/tracer/replay-session/{id}/generate-scenario/", { id: id }),

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
    detail: (id) =>
      apiPath("/simulate/scenarios/{scenario_id}/", { scenario_id: id }),
    edit: (id) =>
      apiPath("/simulate/scenarios/{scenario_id}/edit/", { scenario_id: id }),
    delete: (id) =>
      apiPath("/simulate/scenarios/{scenario_id}/delete/", { scenario_id: id }),
    addRowUsingAi: (scenarioId) =>
      apiPath("/simulate/scenarios/{scenario_id}/add-rows/", {
        scenario_id: scenarioId,
      }),
    addCols: (scenarioId) =>
      apiPath("/simulate/scenarios/{scenario_id}/add-columns/", {
        scenario_id: scenarioId,
      }),
  },
  simulatorAgents: {
    list: apiPath("/simulate/simulator-agents/"),
    create: apiPath("/simulate/simulator-agents/create/"),
    detail: (id) =>
      apiPath("/simulate/simulator-agents/{agent_id}/", { agent_id: id }),
    edit: (id) =>
      apiPath("/simulate/simulator-agents/{agent_id}/edit/", { agent_id: id }),
    delete: (id) =>
      apiPath("/simulate/simulator-agents/{agent_id}/delete/", {
        agent_id: id,
      }),
  },
  agentDefinitions: {
    list: apiPath("/simulate/agent-definitions/"),
    create: apiPath("/simulate/agent-definitions/create/"),
    versions: (id) =>
      apiPath("/simulate/agent-definitions/{agent_id}/versions/", {
        agent_id: id,
      }),
    versionDetail: (id, version) =>
      apiPath("/simulate/agent-definitions/{agent_id}/versions/{version_id}/", {
        agent_id: id,
        version_id: version,
      }),
    createVersion: (id) =>
      apiPath("/simulate/agent-definitions/{agent_id}/versions/create/", {
        agent_id: id,
      }),
    getCallLogs: (id, version) =>
      apiPath(
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/call-executions/",
        { agent_id: id, version_id: version },
      ),
    detail: (id) =>
      apiPath("/simulate/agent-definitions/{agent_id}/", { agent_id: id }),
    delete: apiPath("/simulate/agent-definitions/"),
    getTestAnalytics: (agent, version) =>
      apiPath(
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/eval-summary/",
        { agent_id: agent, version_id: version },
      ),
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
    update: (id) => apiPath("/simulate/api/personas/{id}/", { id: id }),
    delete: (id) => apiPath("/simulate/api/personas/{id}/", { id: id }),
    duplicate: (id) =>
      apiPath("/simulate/api/personas/duplicate/{persona_id}/", {
        persona_id: id,
      }),
  },
  runTests: {
    list: apiPath("/simulate/run-tests/"),
    create: apiPath("/simulate/run-tests/create/"),
    detail: (id) =>
      apiPath("/simulate/run-tests/{run_test_id}/", { run_test_id: id }),
    detailExecutions: (id) =>
      apiPath("/simulate/run-tests/{run_test_id}/executions/", {
        run_test_id: id,
      }),
    detailScenarios: (id) =>
      apiPath("/simulate/run-tests/{run_test_id}/scenarios/", {
        run_test_id: id,
      }),
    runTest: (id) =>
      apiPath("/simulate/run-tests/{run_test_id}/execute/", {
        run_test_id: id,
      }),
    callExecutionDetail: (id) =>
      apiPath("/simulate/call-executions/{call_execution_id}/", {
        call_execution_id: id,
      }),
    callExecutionsByTestRunId: (id) =>
      apiPath("/simulate/run-tests/{run_test_id}/call-executions/", {
        run_test_id: id,
      }),
    callExecutionsExport: (id) =>
      withQuery(apiPath("/simulate/export/{item_id}/", { item_id: id }), {
        type: "runtest",
      }),
    executionDetailsExport: (id) =>
      withQuery(apiPath("/simulate/export/{item_id}/", { item_id: id }), {
        type: "testexecution",
      }),
    addEvals: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/eval-configs/", {
        run_test_id: testId,
      }),
    deleteEvals: (testId, evalConfigId) =>
      apiPath(
        "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/",
        { run_test_id: testId, eval_config_id: evalConfigId },
      ),
    updateTestRun: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/components/", {
        run_test_id: testId,
      }),
    runEvals: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/run-new-evals/", {
        run_test_id: testId,
      }),
    getConfiguredEvalTemplateConfig: (testId, evalConfigId) =>
      apiPath(
        "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/get-structure/",
        { run_test_id: testId, eval_config_id: evalConfigId },
      ),
    updateSimulateEval: (testId, evalConfigId) =>
      apiPath(
        "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/update/",
        { run_test_id: testId, eval_config_id: evalConfigId },
      ),
    getVoiceSDKCode: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/sdk-code/", {
        run_test_id: testId,
      }),
    deleteSimulation: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/delete-test-executions/", {
        run_test_id: testId,
      }),
    rerunSimulation: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/rerun-test-executions/", {
        run_test_id: testId,
      }),
  },
  testExecutions: {
    callDetail: (id) =>
      apiPath("/simulate/call-executions/{call_execution_id}/", {
        call_execution_id: id,
      }),
    list: (id) =>
      apiPath("/simulate/test-executions/{test_execution_id}/", {
        test_execution_id: id,
      }),
    kpis: (id) =>
      apiPath("/simulate/test-executions/{test_execution_id}/kpis/", {
        test_execution_id: id,
      }),
    executionPerformanceSummary: (executionId) =>
      apiPath(
        "/simulate/test-executions/{test_execution_id}/performance-summary/",
        { test_execution_id: executionId },
      ),
    executionAnalytics: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/eval-summary/", {
        run_test_id: testId,
      }),
    criticalIssue: (executionId) =>
      apiPath(
        "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/",
        { test_execution_id: executionId },
      ),
    criticalIssueRefresh: (executionId) =>
      apiPath(
        "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
        { test_execution_id: executionId },
      ),
    compareSummary: (testId) =>
      apiPath("/simulate/run-tests/{run_test_id}/eval-summary-comparison/", {
        run_test_id: testId,
      }),
    flowAnalysis: (executionId) =>
      apiPath(
        "/simulate/call-executions/{call_execution_id}/branch-analysis/",
        { call_execution_id: executionId },
      ),
    cancelExecution: (id) =>
      apiPath("/simulate/test-executions/{test_execution_id}/cancel/", {
        test_execution_id: id,
      }),
    rerunExecution: (id) =>
      apiPath("/simulate/test-executions/{test_execution_id}/rerun-calls/", {
        test_execution_id: id,
      }),
    getDetailLogs: (id) =>
      apiPath("/simulate/call-executions/{call_execution_id}/logs/", {
        call_execution_id: id,
      }),
    getErrorLocalizerTasks: (id) =>
      apiPath(
        "/simulate/call-executions/{call_execution_id}/error-localizer-tasks/",
        { call_execution_id: id },
      ),
    getOptimizerAnalysis: (id) =>
      apiPath(
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/",
        { test_execution_id: id },
      ),
    refreshOptimizerAnalysis: (id) =>
      apiPath(
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        { test_execution_id: id },
      ),
    compareExecutions: (id) =>
      apiPath(
        "/simulate/call-executions/{call_execution_id}/session-comparison/",
        { call_execution_id: id },
      ),
  },
  optimizeSimulate: {
    createOptimization: apiPath("/simulate/api/agent-prompt-optimiser/"),
    getOptimizationDetails: (id) =>
      apiPath("/simulate/api/agent-prompt-optimiser/{id}/", { id }),
    getOptimizationSteps: (id) =>
      apiPath("/simulate/api/agent-prompt-optimiser/{id}/steps/", { id: id }),
    getOptimizationGraph: (id) =>
      apiPath("/simulate/api/agent-prompt-optimiser/{id}/graph/", { id: id }),
    getTrailPrompts: (id, trialId) =>
      apiPath(
        "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/",
        { id: id, trial_id: trialId },
      ),
    getTrialItems: (id, trialId) =>
      apiPath(
        "/simulate/api/agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/",
        { id: id, trial_id: trialId },
      ),
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
    detail: (id) => apiPath("/tracer/saved-views/{id}/", { id: id }),
    update: (id) => apiPath("/tracer/saved-views/{id}/", { id: id }),
    delete: (id) => apiPath("/tracer/saved-views/{id}/", { id: id }),
    duplicate: (id) =>
      apiPath("/tracer/saved-views/{id}/duplicate/", { id: id }),
    reorder: apiPath("/tracer/saved-views/reorder/"),
  },
  sharedLinks: {
    list: apiPath("/tracer/shared-links/"),
    create: apiPath("/tracer/shared-links/"),
    detail: (id) => apiPath("/tracer/shared-links/{id}/", { id: id }),
    update: (id) => apiPath("/tracer/shared-links/{id}/", { id: id }),
    delete: (id) => apiPath("/tracer/shared-links/{id}/", { id: id }),
    addAccess: (id) => apiPath("/tracer/shared-links/{id}/access/", { id: id }),
    removeAccess: (id, accessId) =>
      apiPath("/tracer/shared-links/{id}/access/{access_id}/", {
        id: id,
        access_id: accessId,
      }),
    resolve: (token) => apiPath("/tracer/shared/{token}/", { token: token }),
  },
  organizations: {
    list: apiPath("/accounts/organizations/"),
    switch: apiPath("/accounts/organizations/switch/"),
    current: apiPath("/accounts/organizations/current/"),
    create: apiPath("/accounts/organizations/new/"),
    update: apiPath("/accounts/organizations/update/"),
  },
  errorFeed: {
    list: apiPath("/tracer/feed/issues/"),
    stats: apiPath("/tracer/feed/issues/stats/"),
    detail: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/", { cluster_id: clusterId }),
    update: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/", { cluster_id: clusterId }),
    overview: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/overview/", {
        cluster_id: clusterId,
      }),
    traces: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/traces/", {
        cluster_id: clusterId,
      }),
    trends: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/trends/", {
        cluster_id: clusterId,
      }),
    sidebar: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/sidebar/", {
        cluster_id: clusterId,
      }),
    rootCause: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/root-cause/", {
        cluster_id: clusterId,
      }),
    deepAnalysis: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/deep-analysis/", {
        cluster_id: clusterId,
      }),
    createLinearIssue: (clusterId) =>
      apiPath("/tracer/feed/issues/{cluster_id}/create-linear-issue/", {
        cluster_id: clusterId,
      }),
    linearTeams: apiPath("/tracer/feed/integrations/linear/teams/"),
  },
  promptSimulation: {
    scenarios: apiPath("/simulate/prompt-simulations/scenarios/"),
    simulations: (promptTemplateId) =>
      apiPath("/simulate/prompt-templates/{prompt_template_id}/simulations/", {
        prompt_template_id: promptTemplateId,
      }),
    detail: (promptTemplateId, runTestId) =>
      apiPath(
        "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        { prompt_template_id: promptTemplateId, run_test_id: runTestId },
      ),
    execute: (promptTemplateId, runTestId) =>
      apiPath(
        "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/execute/",
        { prompt_template_id: promptTemplateId, run_test_id: runTestId },
      ),
  },
  gateway: {
    list: apiPath("/agentcc/gateways/"),
    detail: (id) => apiPath("/agentcc/gateways/{id}/", { id: id }),
    update: (id) => apiPath("/agentcc/gateways/{id}/", { id: id }),
    healthCheck: (id) =>
      apiPath("/agentcc/gateways/{id}/health_check/", { id: id }),
    config: (id) => apiPath("/agentcc/gateways/{id}/config/", { id: id }),
    providers: (id) => apiPath("/agentcc/gateways/{id}/providers/", { id: id }),
    reload: (id) => apiPath("/agentcc/gateways/{id}/reload/", { id: id }),
    updateConfig: (id) =>
      apiPath("/agentcc/gateways/{id}/update-config/", { id: id }),
    updateProvider: (id) =>
      apiPath("/agentcc/gateways/{id}/update-provider/", { id: id }),
    removeProvider: (id) =>
      apiPath("/agentcc/gateways/{id}/remove-provider/", { id: id }),
    testPlayground: (id) =>
      apiPath("/agentcc/gateways/{id}/test-playground/", { id: id }),
    toggleGuardrail: (id) =>
      apiPath("/agentcc/gateways/{id}/toggle-guardrail/", { id: id }),
    updateGuardrail: (id) =>
      apiPath("/agentcc/gateways/{id}/update-guardrail/", { id: id }),
    protectTemplates: apiPath("/agentcc/gateways/protect-templates/"),
    setBudget: (id) =>
      apiPath("/agentcc/gateways/{id}/set-budget/", { id: id }),
    removeBudget: (id) =>
      apiPath("/agentcc/gateways/{id}/remove-budget/", { id: id }),
    mcpStatus: (id) =>
      apiPath("/agentcc/gateways/{id}/mcp-status/", { id: id }),
    mcpTools: (id) => apiPath("/agentcc/gateways/{id}/mcp-tools/", { id: id }),
    updateMcpServer: (id) =>
      apiPath("/agentcc/gateways/{id}/update-mcp-server/", { id: id }),
    removeMcpServer: (id) =>
      apiPath("/agentcc/gateways/{id}/remove-mcp-server/", { id: id }),
    updateMcpGuardrails: (id) =>
      apiPath("/agentcc/gateways/{id}/update-mcp-guardrails/", { id: id }),
    testMcpTool: (id) =>
      apiPath("/agentcc/gateways/{id}/test-mcp-tool/", { id: id }),
    mcpResources: (id) =>
      apiPath("/agentcc/gateways/{id}/mcp-resources/", { id: id }),
    mcpPrompts: (id) =>
      apiPath("/agentcc/gateways/{id}/mcp-prompts/", { id: id }),
    apiKeys: apiPath("/agentcc/api-keys/"),
    createApiKey: apiPath("/agentcc/api-keys/"),
    apiKeyDetail: (id) => apiPath("/agentcc/api-keys/{id}/", { id: id }),
    updateApiKey: (id) => apiPath("/agentcc/api-keys/{id}/", { id: id }),
    revokeApiKey: (id) => apiPath("/agentcc/api-keys/{id}/revoke/", { id: id }),
    syncApiKeys: apiPath("/agentcc/api-keys/sync/"),
    requestLogs: apiPath("/agentcc/request-logs/"),
    requestLogDetail: (id) =>
      apiPath("/agentcc/request-logs/{id}/", { id: id }),
    requestLogSearch: apiPath("/agentcc/request-logs/search/"),
    requestLogSessions: apiPath("/agentcc/request-logs/sessions/"),
    requestLogSessionDetail: (sessionId) =>
      apiPath("/agentcc/request-logs/sessions/{session_id}/", {
        session_id: sessionId,
      }),
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
      detail: (cfgId) => apiPath("/agentcc/org-configs/{id}/", { id: cfgId }),
      activate: (cfgId) =>
        apiPath("/agentcc/org-configs/{id}/activate/", { id: cfgId }),
      diff: (cfgId) =>
        apiPath("/agentcc/org-configs/{id}/diff/", { id: cfgId }),
    },
    webhooks: {
      list: apiPath("/agentcc/webhooks/"),
      create: apiPath("/agentcc/webhooks/"),
      detail: (id) => apiPath("/agentcc/webhooks/{id}/", { id: id }),
      update: (id) => apiPath("/agentcc/webhooks/{id}/", { id: id }),
      delete: (id) => apiPath("/agentcc/webhooks/{id}/", { id: id }),
      test: (id) => apiPath("/agentcc/webhooks/{id}/test/", { id: id }),
    },
    webhookEvents: {
      list: apiPath("/agentcc/webhook-events/"),
      detail: (id) => apiPath("/agentcc/webhook-events/{id}/", { id: id }),
      retry: (id) => apiPath("/agentcc/webhook-events/{id}/retry/", { id: id }),
    },
    guardrailFeedback: {
      list: apiPath("/agentcc/guardrail-feedback/"),
      create: apiPath("/agentcc/guardrail-feedback/"),
      detail: (id) => apiPath("/agentcc/guardrail-feedback/{id}/", { id: id }),
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
      detail: (id) => apiPath("/agentcc/sessions/{id}/", { id: id }),
      update: (id) => apiPath("/agentcc/sessions/{id}/", { id: id }),
      delete: (id) => apiPath("/agentcc/sessions/{id}/", { id: id }),
      close: (id) => apiPath("/agentcc/sessions/{id}/close/", { id: id }),
      requests: (id) => apiPath("/agentcc/sessions/{id}/requests/", { id: id }),
    },
    batch: {
      submit: (id) =>
        apiPath("/agentcc/gateways/{id}/submit-batch/", { id: id }),
      get: (id) => apiPath("/agentcc/gateways/{id}/get-batch/", { id: id }),
      cancel: (id) =>
        apiPath("/agentcc/gateways/{id}/cancel-batch/", { id: id }),
    },
    customProperties: {
      list: apiPath("/agentcc/custom-properties/"),
      create: apiPath("/agentcc/custom-properties/"),
      detail: (id) => apiPath("/agentcc/custom-properties/{id}/", { id: id }),
      update: (id) => apiPath("/agentcc/custom-properties/{id}/", { id: id }),
      delete: (id) => apiPath("/agentcc/custom-properties/{id}/", { id: id }),
      validate: apiPath("/agentcc/custom-properties/validate/"),
    },
    emailAlerts: {
      list: apiPath("/agentcc/email-alerts/"),
      create: apiPath("/agentcc/email-alerts/"),
      detail: (id) => apiPath("/agentcc/email-alerts/{id}/", { id: id }),
      update: (id) => apiPath("/agentcc/email-alerts/{id}/", { id: id }),
      delete: (id) => apiPath("/agentcc/email-alerts/{id}/", { id: id }),
      test: (id) => apiPath("/agentcc/email-alerts/{id}/test/", { id: id }),
    },
    shadowExperiments: {
      list: apiPath("/agentcc/shadow-experiments/"),
      create: apiPath("/agentcc/shadow-experiments/"),
      detail: (id) => apiPath("/agentcc/shadow-experiments/{id}/", { id: id }),
      update: (id) => apiPath("/agentcc/shadow-experiments/{id}/", { id: id }),
      delete: (id) => apiPath("/agentcc/shadow-experiments/{id}/", { id: id }),
      pause: (id) =>
        apiPath("/agentcc/shadow-experiments/{id}/pause/", { id: id }),
      resume: (id) =>
        apiPath("/agentcc/shadow-experiments/{id}/resume/", { id: id }),
      complete: (id) =>
        apiPath("/agentcc/shadow-experiments/{id}/complete/", { id: id }),
      stats: (id) =>
        apiPath("/agentcc/shadow-experiments/{id}/stats/", { id: id }),
    },
    shadowResults: {
      list: apiPath("/agentcc/shadow-results/"),
      detail: (id) => apiPath("/agentcc/shadow-results/{id}/", { id: id }),
    },
    providerCredentials: {
      fetchModels: apiPath("/agentcc/provider-credentials/fetch_models/"),
    },
  },
  integrations: {
    connections: {
      list: apiPath("/integrations/connections/"),
      create: apiPath("/integrations/connections/"),
      detail: (id) => apiPath("/integrations/connections/{id}/", { id: id }),
      update: (id) => apiPath("/integrations/connections/{id}/", { id: id }),
      delete: (id) => apiPath("/integrations/connections/{id}/", { id: id }),
      syncNow: (id) =>
        apiPath("/integrations/connections/{id}/sync_now/", { id: id }),
      pause: (id) =>
        apiPath("/integrations/connections/{id}/pause/", { id: id }),
      resume: (id) =>
        apiPath("/integrations/connections/{id}/resume/", { id: id }),
    },
    validate: apiPath("/integrations/connections/validate/"),
    syncLogs: apiPath("/integrations/sync-logs/"),
  },
  agentPlayground: {
    listGraphs: apiPath("/agent-playground/graphs/"),
    createGraph: apiPath("/agent-playground/graphs/"),
    createGraphFromTrace: apiPath("/agent-playground/graphs/from-trace/"),
    deleteGraphs: apiPath("/agent-playground/graphs/delete/"),
    graphDetail: (id) => apiPath("/agent-playground/graphs/{id}/", { id: id }),
    updateGraph: (id) => apiPath("/agent-playground/graphs/{id}/", { id: id }),
    graphVersions: (id) =>
      apiPath("/agent-playground/graphs/{id}/versions/", { id: id }),
    nodeTemplates: apiPath("/agent-playground/node-templates/"),
    versionDetail: (graphId, versionId) =>
      apiPath("/agent-playground/graphs/{id}/versions/{version_id}/", {
        id: graphId,
        version_id: versionId,
      }),
    referenceableGraphs: (id) =>
      apiPath("/agent-playground/graphs/{id}/referenceable-graphs/", {
        id: id,
      }),
    activateVersion: (graphId, versionId) =>
      apiPath("/agent-playground/graphs/{id}/versions/{version_id}/activate/", {
        id: graphId,
        version_id: versionId,
      }),
    graphDataset: (graphId, versionId) =>
      withQuery(
        apiPath("/agent-playground/graphs/{graph_id}/dataset/", {
          graph_id: graphId,
        }),
        {
          version_id: versionId,
        },
      ),
    datasetCell: (graphId, cellId) =>
      apiPath("/agent-playground/graphs/{graph_id}/dataset/cells/{cell_id}/", {
        graph_id: graphId,
        cell_id: cellId,
      }),
    executeDataset: (graphId) =>
      apiPath("/agent-playground/graphs/{graph_id}/dataset/execute/", {
        graph_id: graphId,
      }),
    executionDetail: (graphId, executionId) =>
      apiPath(
        "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
        { graph_id: graphId, execution_id: executionId },
      ),
    nodeExecutionDetail: (executionId, nodeExecutionId) =>
      apiPath(
        "/agent-playground/executions/{execution_id}/nodes/{node_execution_id}/",
        { execution_id: executionId, node_execution_id: nodeExecutionId },
      ),
    graphExecutions: (graphId) =>
      apiPath("/agent-playground/graphs/{graph_id}/executions/", {
        graph_id: graphId,
      }),
    addNode: (graphId, versionId) =>
      apiPath("/agent-playground/graphs/{id}/versions/{version_id}/nodes/", {
        id: graphId,
        version_id: versionId,
      }),
    updateNode: (graphId, versionId, nodeId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
        { id: graphId, version_id: versionId, node_id: nodeId },
      ),
    updatePort: (graphId, versionId, portId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/ports/{port_id}/",
        { id: graphId, version_id: versionId, port_id: portId },
      ),
    getNodeDetail: (graphId, versionId, nodeId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
        { id: graphId, version_id: versionId, node_id: nodeId },
      ),
    createConnection: (graphId, versionId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/",
        { id: graphId, version_id: versionId },
      ),
    deleteConnection: (graphId, versionId, connectionId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/{nc_id}/",
        { id: graphId, version_id: versionId, nc_id: connectionId },
      ),
    deleteNode: (graphId, versionId, nodeId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/",
        { id: graphId, version_id: versionId, node_id: nodeId },
      ),
    possibleEdgeMappings: (graphId, versionId, nodeId) =>
      apiPath(
        "/agent-playground/graphs/{id}/versions/{version_id}/nodes/{node_id}/possible-edge-mappings/",
        { id: graphId, version_id: versionId, node_id: nodeId },
      ),
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
    detail: (id) => apiPath("/accounts/passkeys/{id}/", { id: id }),
    authenticateOptions: apiPath("/accounts/passkey/authenticate/options/"),
    authenticateVerify: apiPath("/accounts/passkey/authenticate/verify/"),
  },
  orgPolicy: {
    twoFactor: apiPath("/accounts/organization/2fa-policy/"),
  },
  falconAI: {
    conversations: apiPath("/falcon-ai/conversations/"),
    conversation: (id) =>
      apiPath("/falcon-ai/conversations/{conversation_id}/", {
        conversation_id: id,
      }),
    feedback: (id) =>
      apiPath("/falcon-ai/messages/{message_id}/feedback/", { message_id: id }),
    connectors: apiPath("/falcon-ai/mcp-connectors/"),
    connector: (id) =>
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/", {
        connector_id: id,
      }),
    connectorDiscover: (id) =>
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/discover/", {
        connector_id: id,
      }),
    connectorTest: (id) =>
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/test/", {
        connector_id: id,
      }),
    connectorTools: (id) =>
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/tools/", {
        connector_id: id,
      }),
    connectorAuth: (id) =>
      apiPath("/falcon-ai/mcp-connectors/{connector_id}/authenticate/", {
        connector_id: id,
      }),
    skills: apiPath("/falcon-ai/skills/"),
    skill: (id) => apiPath("/falcon-ai/skills/{skill_id}/", { skill_id: id }),
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
