export const API_CONTRACT_EXCEPTION_STATUSES = Object.freeze({
  ACTIVE_UNCONTRACTED: "active_uncontracted",
  DEPRECATED_DEAD_REFERENCE: "deprecated_dead_reference",
  ENTERPRISE_CONTRACT_PENDING: "enterprise_contract_pending",
});

// Central allowlist for Management API paths that are intentionally outside
// the generated Swagger surface today. New frontend API calls should use
// apiPath(...) against the generated Swagger surface instead.
export const API_CONTRACT_EXCEPTIONS = Object.freeze({
  "/model-hub/ai_models/create/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management create endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai_models/delete/{id}/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management delete endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai_models/update-baseline/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management baseline endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management list/detail endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/list/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management list endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/performance": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management performance endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/update-metric/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management metric endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/data-points/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point list endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/column-config/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point column config endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/create/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point create endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/metrics/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point metric endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/dataset/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset list/detail endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/column-config/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset column config endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/create/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset create endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/options/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset options endpoint is still used by custom metric screens.",
    next: "Contract the old model dataset API or retire the legacy custom metric flow.",
  },
  "/model-hub/dataset/properties/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset properties endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/properties/{id}/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset property detail endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/summary": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset summary endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/get-model-details/": {
    group: "model-management",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model detail endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
});
