export const API_CONTRACT_EXCEPTION_STATUSES = Object.freeze({
  ACTIVE_UNCONTRACTED: "active_uncontracted",
  DEPRECATED_DEAD_REFERENCE: "deprecated_dead_reference",
  ENTERPRISE_CONTRACT_PENDING: "enterprise_contract_pending",
});

const NEXT_GATEWAY_CONSOLE_STEP =
  "Regenerate swagger.json and the API surface once the Django view and serializer exist, switch the endpoint to apiPath(), and delete this entry.";

const gatewayConsoleEndpoint = (reason) =>
  Object.freeze({
    group: "agentcc",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason,
    next: NEXT_GATEWAY_CONSOLE_STEP,
  });

const CREDIT_RECONCILIATION = gatewayConsoleEndpoint(
  "Credit reconciliation has no route in agentcc/urls_billing.py and is not a served console API.",
);
const MANAGED_CATALOG = gatewayConsoleEndpoint(
  "Gateway console managed-catalog API (endpoints, access, stats, history, refresh and decision replay); the Django view is not built yet.",
);
const CAPABILITIES = gatewayConsoleEndpoint(
  "Gateway console capabilities API; the Django view is not built yet.",
);
const POLICY_ROLLOUT = gatewayConsoleEndpoint(
  "Gateway console staged policy rollout API; the Django view is not built yet.",
);
const ANALYTICS_V2 = gatewayConsoleEndpoint(
  "Gateway console analytics API (billing, agents, failures, health series and reports); the Django view is not built yet.",
);
const RUNS = gatewayConsoleEndpoint(
  "Gateway console agent runs API; the Django view is not built yet.",
);

// Keep this manifest to APIs that are intentionally outside the generated
// Management API contract. New frontend API calls must use apiPath(...).
//
// The entries below are the Datasets + Experiments prototype endpoints
// (PRODUCT-SPEC §6.2, answered by MSW in prototype mode; shapes in
// src/_mock/prototype/CONTRACT.md). Call them with uncontractedApiPath(...).
// They move to the generated contract when the backend ships them.
const DSX_PROTOTYPE = Object.freeze({
  group: "model-hub",
  status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
  reason: "dsx prototype (PRODUCT-SPEC §6.2)",
  next: "Add to the Django swagger when the backend ships these endpoints, then switch callers to apiPath(...)",
});

// The gateway console entries below are registered ahead of their Django
// views so the console pages can be built in parallel; each one moves to
// apiPath(...) when the contracts are regenerated.
export const API_CONTRACT_EXCEPTIONS = Object.freeze({
  "/agentcc/managed-credits/reconciliation/": CREDIT_RECONCILIATION,
  "/agentcc/managed-models/catalog/endpoints/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/access/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/stats/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/history/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/refresh/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/replay/": MANAGED_CATALOG,
  "/agentcc/capabilities/": CAPABILITIES,
  "/agentcc/policy-rollout/": POLICY_ROLLOUT,
  "/agentcc/policy-rollout/stage/": POLICY_ROLLOUT,
  "/agentcc/policy-rollout/promote/": POLICY_ROLLOUT,
  "/agentcc/policy-rollout/abort/": POLICY_ROLLOUT,
  "/agentcc/analytics/billing-summary/": ANALYTICS_V2,
  "/agentcc/analytics/billing-margin/": ANALYTICS_V2,
  "/agentcc/analytics/agents/": ANALYTICS_V2,
  "/agentcc/analytics/failures/": ANALYTICS_V2,
  "/agentcc/analytics/health-series/": ANALYTICS_V2,
  "/agentcc/analytics/report/": ANALYTICS_V2,
  "/agentcc/runs/": RUNS,
  "/agentcc/runs/{run_id}/": RUNS,
  "/model-hub/datasets/{dataset_id}/versions/{version_id}/table/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/versions/{version_id}/changes/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/": {
    group: "model-hub",
    status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
    reason:
      "gated by DSX_DATASET_ROW_SOURCE_ENABLED, off by default; not in the public surface while off",
    next: "When DSX_DATASET_ROW_SOURCE_ENABLED ships ON, regenerate the surface with this route, switch the caller back to apiPath(), and delete this entry.",
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/runs/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/result-reviews/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/results/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  // Round 2 (R2-SPEC §6): I1 iterate / quick test, S1 code session, IM1 import.
  "/model-hub/experiments/v2/{experiment_id}/runs/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/session/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/imports/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  // the interface contract (lane w3-rows-in A7): import jobs and saved sources.
  "/model-hub/datasets/imports/jobs/{job_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/sources/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/{dataset_id}/sources/{source_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch", "delete"],
  },
  "/model-hub/datasets/{dataset_id}/sources/{source_id}/refresh/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  // Round 3 (R3 G0 §B10): side-by-side task compare + review, saved views.
  "/model-hub/datasets/{dataset_id}/task-compare/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/task-compare/review/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  // Round 4 (implementation; CONTRACT.md "Round 4").
  "/model-hub/r4/run-sources/catalog/": { ...DSX_PROTOTYPE, methods: ["get"] },
  "/model-hub/r4/calls/{call_id}/": { ...DSX_PROTOTYPE, methods: ["get"] },
  "/model-hub/r4/calls/{call_id}/audio/{channel}": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/r4/connect/machines/": { ...DSX_PROTOTYPE, methods: ["get"] },
  "/model-hub/r4/connect/machines/{machine_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch"],
  },
  "/model-hub/r4/connect/tokens/": { ...DSX_PROTOTYPE, methods: ["post"] },
  "/model-hub/r4/connect/sessions/{token}/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/r4/connect/sessions/{token}/simulate/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/r4/connect/agents/{agent_id}/try/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/r4/runs/{run_id}/activity/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/r4/runs/{run_id}/control/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/r4/runs/{run_id}/simulate-upload/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/r4/runs/{run_id}/rerun/": { ...DSX_PROTOTYPE, methods: ["post"] },
  "/model-hub/r4/runs/{run_id}/paths/": { ...DSX_PROTOTYPE, methods: ["get"] },
  "/model-hub/r4/datasets/{dataset_id}/voice-summary/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  // the interface contract collaboration, review and access (the interface contract §3; CONTRACT.md "the interface contract").
  "/model-hub/datasets/{dataset_id}/access/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "put", "post"],
  },
  "/model-hub/workspaces/{workspace_id}/sharing-policy/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "put"],
  },
  "/model-hub/audit/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/comments/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/comments/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/comments/{comment_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch", "delete"],
  },
  "/model-hub/datasets/{dataset_id}/assignments/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/{dataset_id}/assignments/{assignment_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch"],
  },
  "/model-hub/datasets/{dataset_id}/translate/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/{dataset_id}/corrections/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/datasets/{dataset_id}/corrections/{correction_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch"],
  },
  "/model-hub/annotation-queues/{queue_id}/invites/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  "/model-hub/annotation-queues/{queue_id}/invites/{invite_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch", "delete"],
  },
  "/model-hub/review-links/{token}/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  "/model-hub/review-links/{token}/items/{item_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  // the interface contract dataset organisation (the interface contract §3.2; CONTRACT.md "the interface contract").
  "/model-hub/datasets/query/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  // the interface contract (EV-03): what adopting an eval version would change on a run (the
  // flip preview; CONTRACT.md §6.2 "Eval version preview").
  "/model-hub/eval-templates/{template_id}/versions/{version}/preview/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  // Custom result-column definitions and their per-row values.
  "/model-hub/datasets/{dataset_id}/result-columns/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "put"],
  },
  "/model-hub/datasets/{dataset_id}/result-columns/values/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  // Run metrics, classification, comparison by key, and best-of-N selection.
  "/model-hub/datasets/{dataset_id}/derived-metrics/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "put"],
  },
  "/model-hub/datasets/{dataset_id}/classification/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "put"],
  },
  "/model-hub/results/match/": { ...DSX_PROTOTYPE, methods: ["get"] },
  "/model-hub/datasets/{dataset_id}/best-of/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "post"],
  },
  // Tag registry, experiment tags, leaderboards, and run traces.
  "/model-hub/tags/": { ...DSX_PROTOTYPE, methods: ["get", "post"] },
  "/model-hub/tags/{tag_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["patch", "delete"],
  },
  "/model-hub/experiments/v2/{experiment_id}/tags/": {
    ...DSX_PROTOTYPE,
    methods: ["put"],
  },
  "/model-hub/leaderboards/": { ...DSX_PROTOTYPE, methods: ["get", "post"] },
  "/model-hub/leaderboards/{leaderboard_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "patch", "delete"],
  },
  "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/traces/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
  // Expression-column creation on a dataset.
  "/model-hub/develops/{dataset_id}/add_expression_column/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  // Record the result of an externally executed run.
  "/model-hub/experiments/v2/{experiment_id}/runs/{run_id}/record/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  // Evaluator decision trees, composites, agreement, judge refinement,
  // and remote scorers.
  // shared-edit(F69): Agreement uses the generated GET contract.
  "/model-hub/eval-templates/{template_id}/improve/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/eval-templates/create-remote/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/eval-templates/{template_id}/remote/": {
    ...DSX_PROTOTYPE,
    methods: ["get", "patch"],
  },
  "/model-hub/eval-templates/remote/test/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/eval-templates/{template_id}/remote/score-run/": {
    ...DSX_PROTOTYPE,
    methods: ["post"],
  },
  "/model-hub/eval-templates/remote/jobs/{job_id}/": {
    ...DSX_PROTOTYPE,
    methods: ["get"],
  },
});
