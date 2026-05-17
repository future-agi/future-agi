export const LEGACY_API_STATUSES = Object.freeze({
  ACTIVE_UNCONTRACTED: "active_uncontracted",
  DEPRECATED_DEAD_REFERENCE: "deprecated_dead_reference",
  EE_UNCONTRACTED: "ee_uncontracted",
});

// Central allowlist for pre-contract Management API paths. New frontend API
// calls should use apiPath(...) against the generated Swagger surface instead.
export const LEGACY_API_SURFACE = Object.freeze({
  "/falcon-ai/conversations/{id}/messages/": {
    group: "falcon-ai",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Falcon conversation messages are still used by the Falcon UI but are not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for Falcon messages, then move this path to apiPath(...).",
  },
  "/model-hub/ai_models/create/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management create endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai_models/delete/{id}/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management delete endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai_models/update-baseline/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management baseline endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management list/detail endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/list/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management list endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/performance": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management performance endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/ai-models/update-metric/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model-management metric endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/custom-metric/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Custom metric collection is still used by the legacy model performance UI.",
    next: "Add serializers/OpenAPI coverage for custom metric collection, then move this path to apiPath(...).",
  },
  "/model-hub/data-points/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point list endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/column-config/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point column config endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/create/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point create endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/data-points/metrics/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model data-point metric endpoint is still used by dataset detail screens.",
    next: "Contract the old data-points API or migrate dataset detail screens to the newer dataset APIs.",
  },
  "/model-hub/dataset/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset list/detail endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/column-config/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset column config endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/create/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset create endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/options/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset options endpoint is still used by custom metric screens.",
    next: "Contract the old model dataset API or retire the legacy custom metric flow.",
  },
  "/model-hub/dataset/properties/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset properties endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/properties/{id}/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset property detail endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/dataset/summary": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model dataset summary endpoint is still used by old model dataset screens.",
    next: "Contract the old model dataset API or retire the legacy model dataset screens.",
  },
  "/model-hub/datasets/{dataset_id}/execute-code/": {
    group: "datasets",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Dynamic column execute-code route is no longer registered in backend URLs.",
    next: "Remove the frontend execute-code affordance or replace it with a contracted dynamic-column operation.",
  },
  "/model-hub/develops/get_preset_eval_structure/{template_id}/": {
    group: "develops",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Preset eval structure route is no longer registered in backend URLs.",
    next: "Remove the old preset eval call site or replace it with the contracted eval structure endpoint.",
  },
  "/model-hub/eval-playground-logs/": {
    group: "evals",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Eval playground logs route is no longer registered in backend URLs.",
    next: "Remove the old eval playground log call site or add a contracted replacement endpoint.",
  },
  "/model-hub/event-names/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model event metadata endpoint is still referenced by old model screens.",
    next: "Contract the old event metadata API or migrate the old model screens.",
  },
  "/model-hub/events/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model events endpoint is still referenced by old model screens.",
    next: "Contract the old event metadata API or migrate the old model screens.",
  },
  "/model-hub/experiments/{experiment_id}/re-run/{col_id}/": {
    group: "experiments",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Old experiment column rerun route is no longer registered in backend URLs.",
    next: "Remove the old call site and use the contracted V2 rerun-cells endpoint.",
  },
  "/model-hub/get-eval-feedback": {
    group: "evals",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason: "Old eval feedback route is no longer registered in backend URLs.",
    next: "Remove the old eval feedback call site or use the contracted feedback endpoints.",
  },
  "/model-hub/get-model-details/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Old model detail endpoint is still referenced by the legacy Models UI.",
    next: "Contract the old model-management API or retire the legacy Models UI.",
  },
  "/model-hub/performance/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model performance graph endpoint is still used by the model Performance page.",
    next: "Add serializers/OpenAPI coverage for performance graph data, then move this path to apiPath(...).",
  },
  "/model-hub/run-eval": {
    group: "evals",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Standalone run-eval route is no longer registered in backend URLs.",
    next: "Remove the old standalone run-eval call site or replace it with contracted eval execution APIs.",
  },
  "/model-hub/unique-properties/": {
    group: "model-management",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Legacy model unique-property endpoint is still referenced by old model screens.",
    next: "Contract the old event metadata API or migrate the old model screens.",
  },
  "/simulate/run-tests/{id}/execute/": {
    group: "simulate",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Run-test execute action is still used by the simulation UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for run-test execution, then move this path to apiPath(...).",
  },
  "/tracer/replay-session/prefetch-agent-data/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Replay-session prefetch action is still used by replay UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for replay prefetch, then move this path to apiPath(...).",
  },
  "/tracer/trace-error-analysis/clusters/{id}/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason:
      "Trace error cluster detail route is commented out in backend URLs.",
    next: "Remove the old feed call site or restore the route with serializers and OpenAPI coverage.",
  },
  "/tracer/trace-error-analysis/clusters/feed/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.DEPRECATED_DEAD_REFERENCE,
    reason: "Trace error cluster feed route is commented out in backend URLs.",
    next: "Remove the old feed call site or restore the route with serializers and OpenAPI coverage.",
  },
  "/tracer/user-alerts/{id}/fetch_logs/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Monitor log action is still used by alert UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for monitor log actions, then move this path to apiPath(...).",
  },
  "/tracer/user-alerts/create_graph/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Monitor graph action is still used by alert UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for monitor graph actions, then move this path to apiPath(...).",
  },
  "/tracer/user-alerts/duplicate/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Monitor duplicate action is still used by alert UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for monitor duplicate actions, then move this path to apiPath(...).",
  },
  "/tracer/user-alerts/get_metric_details/": {
    group: "tracer",
    status: LEGACY_API_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "Monitor metric metadata action is still used by alert UI but is not exposed in Swagger.",
    next: "Add serializers/OpenAPI coverage for monitor metric metadata, then move this path to apiPath(...).",
  },
  "/usage/available-months/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage billing available-months endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/create-topup-session/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Top-up billing endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE billing endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/billing-overview/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 billing overview endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/budgets/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 budgets endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/budgets/{id}/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 budget detail endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/invoices/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 invoices endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/invoices/{id}/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 invoice detail endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/notifications/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 notifications endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/plans-and-addons/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 plans/addons endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/usage-overview/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 overview endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/usage-time-series/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 time-series endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
  "/usage/v2/usage-workspace-breakdown/": {
    group: "usage",
    status: LEGACY_API_STATUSES.EE_UNCONTRACTED,
    reason:
      "Usage V2 workspace breakdown endpoint is EE-backed and not fully exposed in Swagger.",
    next: "Expose the EE usage V2 endpoint in Swagger or hide this UI when the endpoint is unavailable.",
  },
});
