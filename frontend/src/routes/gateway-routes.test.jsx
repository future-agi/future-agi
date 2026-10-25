import { describe, expect, it } from "vitest";
import { matchRoutes } from "react-router-dom";

import { endpoints } from "src/utils/axios";
import { useNavGatewayData } from "src/layouts/dashboard/config-navigation";
import { paths } from "./paths";
import { dashboardRoutes } from "./sections/dashboard";

// The console pages whose content is a placeholder, and the MCP page: the
// gateway connects only the MCP servers in its own config file, never an
// organization's, so nothing that page saves or tests reaches a server. They
// are hidden for the launch: no nav entry and no route, so the app's
// catch-all (routes/sections/index.jsx) sends their URLs to /404.
// Give a page its route and nav entry back only with its real content.
const HIDDEN_PAGE_URLS = [
  "/dashboard/gateway/playground",
  "/dashboard/gateway/capabilities",
  "/dashboard/gateway/runs",
  "/dashboard/gateway/runs/run-42",
  "/dashboard/gateway/billing",
  "/dashboard/gateway/billing/credits",
  "/dashboard/gateway/billing/history",
  "/dashboard/gateway/billing/usage",
  "/dashboard/gateway/policies",
  "/dashboard/gateway/policies/access",
  "/dashboard/gateway/policies/data",
  "/dashboard/gateway/policies/presets",
  "/dashboard/gateway/policies/versions",
  "/dashboard/gateway/policies/rollout",
  "/dashboard/gateway/mcp",
  "/dashboard/gateway/mcp/registry",
];
const HIDDEN_NAV_TITLES = [
  "Playground",
  "Capabilities",
  "Policies",
  "Runs",
  "Billing & Credits",
  "MCP Tools",
];

// Real gateway pages, so a route table that matches nothing fails here
// rather than passing the hidden-page check by accident.
const ROUTED_URLS = [
  "/dashboard/gateway",
  "/dashboard/gateway/models",
  "/dashboard/gateway/models/auto",
  "/dashboard/gateway/logs",
  "/dashboard/gateway/budgets",
  "/dashboard/gateway/settings",
];

const routeTables = [
  ["cloud", dashboardRoutes(null, null, { isCloud: true })],
  ["self-hosted", dashboardRoutes(null, null, { isCloud: false })],
];

describe("gateway console paths", () => {
  const gw = paths.dashboard.gateway;

  it("builds the runs, playground and capabilities URLs", () => {
    expect(gw.runs).toBe("/dashboard/gateway/runs");
    expect(gw.runDetail("run-42")).toBe("/dashboard/gateway/runs/run-42");
    expect(gw.playground).toBe("/dashboard/gateway/playground");
    expect(gw.capabilities).toBe("/dashboard/gateway/capabilities");
  });

  it("encodes a run id that is not URL-safe", () => {
    // Run ids come from the caller's x-agentcc-run-id header.
    expect(gw.runDetail("team/run 1?x")).toBe(
      "/dashboard/gateway/runs/team%2Frun%201%3Fx",
    );
  });

  it("builds one URL per billing and policies tab", () => {
    expect(gw.billing).toEqual({
      root: "/dashboard/gateway/billing",
      credits: "/dashboard/gateway/billing/credits",
      history: "/dashboard/gateway/billing/history",
      usage: "/dashboard/gateway/billing/usage",
    });
    expect(gw.policies).toEqual({
      root: "/dashboard/gateway/policies",
      access: "/dashboard/gateway/policies/access",
      data: "/dashboard/gateway/policies/data",
      presets: "/dashboard/gateway/policies/presets",
      versions: "/dashboard/gateway/policies/versions",
      rollout: "/dashboard/gateway/policies/rollout",
    });
  });
});

describe("gateway console routes", () => {
  describe.each(routeTables)("on %s", (_, routes) => {
    it.each(HIDDEN_PAGE_URLS)("routes no page at %s", (url) => {
      expect(matchRoutes(routes, url)).toBeNull();
    });

    it.each(ROUTED_URLS)("still routes %s", (url) => {
      expect(matchRoutes(routes, url)).not.toBeNull();
    });
  });
});

describe("gateway console navigation", () => {
  const groups = useNavGatewayData();
  const items = groups.flatMap((group) => group.items);
  const titles = items.map((item) => item.title);

  it.each(HIDDEN_NAV_TITLES)("has no %s entry", (title) => {
    expect(titles).not.toContain(title);
  });

  it("keeps the shipped entries, Models included, in order", () => {
    expect(titles).toEqual([
      "Overview",
      "Providers",
      "Models",
      "API Keys",
      "Guardrails",
      "Fallbacks",
      "Request Logs",
      "Analytics",
      "Monitoring",
      "Sessions",
      "Budgets",
      "Webhooks",
      "Custom Properties",
      "Settings",
    ]);
  });

  it.each(routeTables)(
    "links every entry to a routed page on %s",
    (_, routes) => {
      for (const item of items) {
        expect(HIDDEN_PAGE_URLS).not.toContain(item.path);
        expect(matchRoutes(routes, item.path)).not.toBeNull();
      }
    },
  );

  it("still links Models to its page", () => {
    expect(items.find((item) => item.title === "Models").path).toBe(
      paths.dashboard.gateway.models,
    );
  });
});

describe("gateway console endpoint keys", () => {
  const gw = endpoints.gateway;

  it("registers every console endpoint with its Django URL", () => {
    expect({
      managedCredits: gw.managedCredits,
      managedCreditEntries: gw.managedCreditEntries,
      managedCreditStatement: gw.managedCreditStatement,
      managedCreditCheckout: gw.managedCreditCheckout,
      managedCreditCheckoutStatus: gw.managedCreditCheckoutStatus("cs_1"),
      managedCreditSettings: gw.managedCreditSettings,
      managedCreditGrant: gw.managedCreditGrant,
      managedCreditAdjust: gw.managedCreditAdjust,
      managedCreditRefund: gw.managedCreditRefund,
      managedCreditReconciliation: gw.managedCreditReconciliation,
      managedCatalogEndpoints: gw.managedCatalogEndpoints,
      managedCatalogAccess: gw.managedCatalogAccess,
      managedCatalogStats: gw.managedCatalogStats,
      managedCatalogHistory: gw.managedCatalogHistory,
      managedCatalogRefresh: gw.managedCatalogRefresh,
      managedCatalogReplay: gw.managedCatalogReplay,
      capabilities: gw.capabilities,
      playgroundRun: gw.playgroundRun,
      policyRollout: gw.policyRollout,
      policyRolloutStage: gw.policyRolloutStage,
      policyRolloutPromote: gw.policyRolloutPromote,
      policyRolloutAbort: gw.policyRolloutAbort,
      analyticsBillingSummary: gw.analyticsBillingSummary,
      analyticsBillingMargin: gw.analyticsBillingMargin,
      analyticsAgents: gw.analyticsAgents,
      analyticsFailures: gw.analyticsFailures,
      analyticsHealthSeries: gw.analyticsHealthSeries,
      analyticsReport: gw.analyticsReport,
      runs: gw.runs,
      runDetail: gw.runDetail("run-42"),
      providerCredentialRotate: gw.providerCredentialRotate("cred-7"),
    }).toEqual({
      managedCredits: "/agentcc/managed-credits/",
      managedCreditEntries: "/agentcc/managed-credits/entries/",
      managedCreditStatement: "/agentcc/managed-credits/statement/",
      managedCreditCheckout: "/agentcc/managed-credits/checkout/",
      managedCreditCheckoutStatus: "/agentcc/managed-credits/checkout/cs_1/",
      managedCreditSettings: "/agentcc/managed-credits/settings/",
      managedCreditGrant: "/agentcc/managed-credits/grant/",
      managedCreditAdjust: "/agentcc/managed-credits/adjust/",
      managedCreditRefund: "/agentcc/managed-credits/refund/",
      managedCreditReconciliation: "/agentcc/managed-credits/reconciliation/",
      managedCatalogEndpoints: "/agentcc/managed-models/catalog/endpoints/",
      managedCatalogAccess: "/agentcc/managed-models/catalog/access/",
      managedCatalogStats: "/agentcc/managed-models/catalog/stats/",
      managedCatalogHistory: "/agentcc/managed-models/catalog/history/",
      managedCatalogRefresh: "/agentcc/managed-models/catalog/refresh/",
      managedCatalogReplay: "/agentcc/managed-models/catalog/replay/",
      capabilities: "/agentcc/capabilities/",
      playgroundRun: "/agentcc/playground/run/",
      policyRollout: "/agentcc/policy-rollout/",
      policyRolloutStage: "/agentcc/policy-rollout/stage/",
      policyRolloutPromote: "/agentcc/policy-rollout/promote/",
      policyRolloutAbort: "/agentcc/policy-rollout/abort/",
      analyticsBillingSummary: "/agentcc/analytics/billing-summary/",
      analyticsBillingMargin: "/agentcc/analytics/billing-margin/",
      analyticsAgents: "/agentcc/analytics/agents/",
      analyticsFailures: "/agentcc/analytics/failures/",
      analyticsHealthSeries: "/agentcc/analytics/health-series/",
      analyticsReport: "/agentcc/analytics/report/",
      runs: "/agentcc/runs/",
      runDetail: "/agentcc/runs/run-42/",
      providerCredentialRotate: "/agentcc/provider-credentials/cred-7/rotate/",
    });
  });

  it("encodes path parameters", () => {
    expect(gw.runDetail("team/run 1")).toBe("/agentcc/runs/team%2Frun%201/");
    expect(gw.managedCreditCheckoutStatus("cs/1")).toBe(
      "/agentcc/managed-credits/checkout/cs%2F1/",
    );
  });

  it("refuses to build a URL without its path parameter", () => {
    expect(() => gw.runDetail("")).toThrow(/run_id/);
    expect(() => gw.managedCreditCheckoutStatus(undefined)).toThrow(
      /session_id/,
    );
  });
});
