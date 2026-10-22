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

const MANAGED_CREDITS = gatewayConsoleEndpoint(
  "Gateway console managed-credit API (balance, ledger, statement, top-up, settings and staff adjustments); the Django view is not built yet.",
);
const MANAGED_CATALOG = gatewayConsoleEndpoint(
  "Gateway console managed-catalog API (endpoints, access, stats, history, refresh and decision replay); the Django view is not built yet.",
);
const CAPABILITIES = gatewayConsoleEndpoint(
  "Gateway console capabilities API; the Django view is not built yet.",
);
const PLAYGROUND = gatewayConsoleEndpoint(
  "Gateway console playground API; the Django view is not built yet.",
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
// The gateway console entries below are registered ahead of their Django
// views so the console pages can be built in parallel; each one moves to
// apiPath(...) when the contracts are regenerated.
export const API_CONTRACT_EXCEPTIONS = Object.freeze({
  "/agentcc/managed-credits/": MANAGED_CREDITS,
  "/agentcc/managed-credits/entries/": MANAGED_CREDITS,
  "/agentcc/managed-credits/statement/": MANAGED_CREDITS,
  "/agentcc/managed-credits/checkout/": MANAGED_CREDITS,
  "/agentcc/managed-credits/checkout/{session_id}/": MANAGED_CREDITS,
  "/agentcc/managed-credits/settings/": MANAGED_CREDITS,
  "/agentcc/managed-credits/grant/": MANAGED_CREDITS,
  "/agentcc/managed-credits/adjust/": MANAGED_CREDITS,
  "/agentcc/managed-credits/refund/": MANAGED_CREDITS,
  "/agentcc/managed-credits/reconciliation/": MANAGED_CREDITS,
  "/agentcc/managed-models/catalog/endpoints/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/access/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/stats/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/history/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/refresh/": MANAGED_CATALOG,
  "/agentcc/managed-models/catalog/replay/": MANAGED_CATALOG,
  "/agentcc/capabilities/": CAPABILITIES,
  "/agentcc/playground/run/": PLAYGROUND,
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
});
