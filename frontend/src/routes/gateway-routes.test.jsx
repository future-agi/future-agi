import React, { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import {
  MemoryRouter,
  Outlet,
  matchRoutes,
  useLocation,
  useRoutes,
} from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import axiosInstance, { endpoints } from "src/utils/axios";
import { gatewayRailGroups } from "src/sections/gateway/shell/gateway-rail";
import {
  HIDDEN_GATEWAY_PAGES,
  HIDDEN_MCP_TABS,
} from "src/sections/gateway/shell/page-status";
import { MCP_TABS } from "src/sections/gateway/mcp/tabs";
import { paths } from "./paths";
import { dashboardRoutes } from "./sections/dashboard";

// The release gate for the AI Gateway console's routes and rail:
// - every page the rail lists is routed, on cloud and self-hosted;
// - a hidden page (sections/gateway/shell/page-status.js) is in no navigation
//   and its URL redirects to the nearest page that works, query and hash kept;
// - the MCP page and Integrations are routed and in the rail, and a link to a
//   hidden MCP tab opens the MCP overview;
// - Runs, the playground hand-off and the old billing URLs stay routed;
// - nothing reached says "coming soon", "in this build" or "preview".

const { gatewaysResponse, managedResponse, mcpStatusResponse } = vi.hoisted(
  () => ({
    gatewaysResponse: { current: { result: [] } },
    // What the managed-models status answers: offered unless a case says not.
    managedResponse: {
      current: { status: true, result: { available: true } },
    },
    // What gw-1's MCP status answers: unanswered (a failed read) unless a
    // case sets it.
    mcpStatusResponse: { current: null },
  }),
);

// GatewayProvider loads the org's gateway through the axios instance; the
// guard in front of every gateway page decides on that answer. Only that URL
// is answered. Any other request rejects, so a page that fetches on mount sees
// a failed request (never the gateway list, never a live call); a page that
// needs data for its test adds its URL here.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  const unmocked = (method) =>
    vi.fn(async (url) => {
      throw new Error(`unmocked ${method} ${url}`);
    });
  const unmockedGet = unmocked("GET");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url, ...rest) => {
        if (url === actual.endpoints.gateway.list)
          return { data: gatewaysResponse.current };
        if (url === actual.endpoints.gateway.managedModels)
          return { data: managedResponse.current };
        if (
          mcpStatusResponse.current &&
          url === actual.endpoints.gateway.mcpStatus("gw-1")
        )
          return { data: mcpStatusResponse.current };
        return unmockedGet(url, ...rest);
      }),
      post: unmocked("POST"),
      put: unmocked("PUT"),
      patch: unmocked("PATCH"),
      delete: unmocked("DELETE"),
    },
  };
});

const PLACEHOLDER = /coming soon|in this build|preview/i;
// GatewayGuard's no-gateway state (components/GatewayEmptyState.jsx): an org
// with no gateway is told the gateway is not connected, never that one is
// being provisioned.
const GATEWAY_GUARD_EMPTY = "Gateway page unavailable";

const testTheme = createTheme({ palette: palette("light") });
const gw = paths.dashboard.gateway;

/** @type {Array<[string, boolean]>} */
const MODES = [
  ["cloud", true],
  ["self-hosted", false],
];

function gatewayRoute(isCloud) {
  const [dashboardRoute] = dashboardRoutes(null, null, { isCloud });
  return dashboardRoute.children.find((child) => child.path === "gateway");
}

// MemoryRouter rather than a data router: the data router builds a fetch
// Request on every navigation, and jsdom's AbortSignal is not one Node's
// Request accepts, so a <Navigate> would never land.
function renderGatewayUrl(url, { isCloud = true } = {}) {
  const location = { pathname: null, search: null, hash: null };
  const LocationProbe = () => {
    ({
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
    } = useLocation());
    return null;
  };
  const routes = [
    {
      path: "/dashboard",
      element: (
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
      ),
      children: [
        gatewayRoute(isCloud),
        { path: "*", element: <div data-testid="outside-gateway" /> },
      ],
    },
  ];
  const GatewayRoutes = () => useRoutes(routes);

  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider theme={testTheme}>
          <MemoryRouter initialEntries={[url]}>
            <LocationProbe />
            <GatewayRoutes />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return location;
}

// Every hidden page URL, with the page it redirects to.
const HIDDEN_PAGE_URLS = [
  [gw.capabilities, HIDDEN_GATEWAY_PAGES.capabilities.redirectTo],
  [gw.policies.root, HIDDEN_GATEWAY_PAGES.policies.redirectTo],
  ...Object.entries(gw.policies)
    .filter(([tab]) => tab !== "root")
    .map(([, url]) => [url, HIDDEN_GATEWAY_PAGES.policies.redirectTo]),
  [`${gw.policies.root}/bogus`, HIDDEN_GATEWAY_PAGES.policies.redirectTo],
];
const HIDDEN_RAIL_TITLES = [
  "Capabilities",
  "Policies",
  "Playground",
  "Billing & credits",
];

// Pages that are routed on every deployment, so a route table that matches
// nothing fails here rather than passing a hidden-page check by accident.
// Runs reads request-log run ids, the playground hands off to the Prompts
// workbench, and the old billing URLs redirect.
const ROUTED_URLS = [
  gw.overview,
  gw.models,
  `${gw.models}/auto`,
  gw.integrations,
  gw.mcp,
  `${gw.mcp}/servers`,
  `${gw.models}/openai/gpt-4o`,
  gw.logs,
  gw.budgets,
  gw.settings,
  gw.runs,
  gw.runDetail("run-42"),
  gw.playground,
  gw.billing.root,
  gw.billing.credits,
  `${gw.billing.root}/bogus`,
];

// The old gateway billing URLs, with every tab and an unknown one.
const BILLING_URLS = [...Object.values(gw.billing), `${gw.billing.root}/bogus`];
const billingTarget = () => paths.dashboard.settings.billing;

describe("gateway console test requests", () => {
  // A page that later replaces a placeholder and fetches on mount must get a
  // failed request here, not the gateway list and not a live call.
  it("answers only the gateway list and rejects every other request", async () => {
    gatewaysResponse.current = { result: [{ id: "gw-1" }] };
    await expect(axiosInstance.get(endpoints.gateway.list)).resolves.toEqual({
      data: { result: [{ id: "gw-1" }] },
    });
    await expect(axiosInstance.get(endpoints.gateway.runs)).rejects.toThrow(
      "unmocked GET /agentcc/runs/",
    );
    await expect(
      axiosInstance.post(endpoints.gateway.managedCreditCheckout, {}),
    ).rejects.toThrow("unmocked POST /agentcc/managed-credits/checkout/");
  });
});

describe("gateway console paths", () => {
  it("keeps the runs, playground and capabilities URLs", () => {
    expect(gw.runs).toBe("/dashboard/gateway/runs");
    expect(gw.runDetail("run-42")).toBe("/dashboard/gateway/runs/run-42");
    expect(gw.playground).toBe("/dashboard/gateway/playground");
    expect(gw.capabilities).toBe("/dashboard/gateway/capabilities");
    expect(gw.mcp).toBe("/dashboard/gateway/mcp");
    expect(gw.integrations).toBe("/dashboard/gateway/integrations");
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

describe("gateway console hidden pages", () => {
  it("hides exactly the pages this gateway and API do not serve", () => {
    expect(Object.keys(HIDDEN_GATEWAY_PAGES).sort()).toEqual([
      "capabilities",
      "policies",
    ]);
    expect(HIDDEN_GATEWAY_PAGES.capabilities.redirectTo).toBe(gw.models);
    expect(HIDDEN_GATEWAY_PAGES.policies.redirectTo).toBe(gw.guardrails.root);
  });
});

describe.each(MODES)("gateway console routes on %s", (_mode, isCloud) => {
  beforeEach(() => {
    gatewaysResponse.current = { result: [{ id: "gw-1", name: "Default" }] };
  });

  const routes = dashboardRoutes(null, null, { isCloud });

  it.each(ROUTED_URLS)("routes %s", (url) => {
    expect(matchRoutes(routes, url)).not.toBeNull();
  });

  it.each(BILLING_URLS)(
    "sends %s on, keeping the query and hash",
    async (url) => {
      const location = renderGatewayUrl(
        `${url}?topup=success&session_id=cs_1#ai-gateway-credits`,
        { isCloud },
      );

      await waitFor(() =>
        expect(location.pathname).toBe(billingTarget(isCloud)),
      );
      expect(location.search).toBe("?topup=success&session_id=cs_1");
      expect(location.hash).toBe("#ai-gateway-credits");
      expect(document.body.textContent).not.toMatch(PLACEHOLDER);
    },
  );

  it("sends the billing URL on even before the org has a gateway", async () => {
    gatewaysResponse.current = { result: [] };
    const location = renderGatewayUrl(gw.billing.history, { isCloud });

    await waitFor(() => expect(location.pathname).toBe(billingTarget(isCloud)));
  });

  it.each(HIDDEN_PAGE_URLS)(
    "redirects the hidden page %s to %s, keeping the query and hash",
    async (url, target) => {
      const location = renderGatewayUrl(`${url}?x=1#top`, { isCloud });

      await waitFor(() => expect(location.pathname).toBe(target));
      expect(location.search).toBe("?x=1");
      expect(location.hash).toBe("#top");
      await waitFor(() =>
        expect(screen.queryByText(GATEWAY_GUARD_EMPTY)).not.toBeInTheDocument(),
      );
      expect(document.body.textContent).not.toMatch(PLACEHOLDER);
    },
  );

  // A deployment with no managed models has no Catalog: the capabilities
  // URL opens the Overview there, and the Catalog where managed models are
  // offered.
  it.each([
    ["offers no managed models", { available: false }, gw.overview],
    ["offers managed models", { available: true }, gw.models],
  ])(
    "sends the capabilities URL of a deployment that %s to its page",
    async (_what, result, target) => {
      managedResponse.current = { status: true, result };
      try {
        const location = renderGatewayUrl(`${gw.capabilities}?x=1#top`, {
          isCloud,
        });
        await waitFor(() => expect(location.pathname).toBe(target));
        expect(location.search).toBe("?x=1");
        expect(location.hash).toBe("#top");
      } finally {
        managedResponse.current = { status: true, result: { available: true } };
      }
    },
  );

  it.each(HIDDEN_PAGE_URLS)(
    "keeps the hidden page %s behind GatewayGuard when the org has no gateway",
    async (url) => {
      gatewaysResponse.current = { result: [] };
      const location = renderGatewayUrl(url, { isCloud });

      expect(await screen.findByText(GATEWAY_GUARD_EMPTY)).toBeInTheDocument();
      expect(location.pathname).toBe(new URL(url, "http://x").pathname);
    },
  );

  // One playground: the gateway URL hands off to the Prompts workbench
  // (sections/gateway/playground/PlaygroundHandoff.test.jsx covers the
  // handoff itself).
  it("sends the playground without a model to the Prompts list", async () => {
    const location = renderGatewayUrl(gw.playground, { isCloud });

    await waitFor(() =>
      expect(location.pathname).toBe("/dashboard/workbench/all"),
    );
    expect(document.body.textContent).not.toMatch(PLACEHOLDER);
  });

  it("hands a named model to Prompts instead of a gateway playground", async () => {
    renderGatewayUrl(`${gw.playground}?model=openai%2Fgpt-4o`, { isCloud });

    // The test's API refuses the prompt draft, so the handoff says so.
    expect(
      await screen.findByText("Could not open the Prompts playground"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(PLACEHOLDER);
  });
});

// The gateway connects the MCP servers an organization saves on this build,
// so the MCP page is shown, and Integrations lists the catalog's apps. A
// link to an MCP tab with no backend (HIDDEN_MCP_TABS), by query or by path,
// opens the MCP overview and drops the tab from the URL.
describe.each(MODES)(
  "gateway MCP and Integrations pages on %s",
  (_m, isCloud) => {
    beforeEach(() => {
      gatewaysResponse.current = { result: [{ id: "gw-1", name: "Default" }] };
      mcpStatusResponse.current = null;
    });

    it("opens the MCP page with its tabs, Virtual servers, Tool calls and Credentials among them", async () => {
      // Every tab, MCP clients and Connect included: the deployment enables
      // client OAuth and client installs (MCPManagementSection shows each of
      // those tabs only on an explicit true).
      mcpStatusResponse.current = {
        status: true,
        result: {
          enabled: true,
          features: {
            client_oauth: true,
            client_installs: true,
            tool_call_explorer: true,
          },
        },
      };
      const location = renderGatewayUrl(`${gw.mcp}?x=1#top`, { isCloud });

      expect(
        await screen.findByRole(
          "heading",
          { level: 1, name: "MCP tools" },
          // The first render loads the lazy page.
          { timeout: 15000 },
        ),
      ).toBeInTheDocument();
      expect(location.pathname).toBe(gw.mcp);
      expect(location.search).toBe("?x=1");
      // MCP clients joins the bar once the MCP status read answers.
      await screen.findByRole("tab", { name: "MCP clients" });
      const labels = screen.getAllByRole("tab").map((tab) => tab.textContent);
      expect(labels).toEqual(MCP_TABS.map((tab) => tab.label));
      for (const label of [
        "Servers",
        "Virtual servers",
        "Tool calls",
        "Credentials",
      ]) {
        expect(labels).toContain(label);
      }
      expect(document.body.textContent).not.toMatch(PLACEHOLDER);
    });

    it.each(Object.keys(HIDDEN_MCP_TABS))(
      "opens the MCP overview for a link to the hidden %s tab",
      async (slug) => {
        const byQuery = renderGatewayUrl(`${gw.mcp}?tab=${slug}&x=1`, {
          isCloud,
        });
        await waitFor(() => expect(byQuery.search).toBe("?x=1"));
        expect(byQuery.pathname).toBe(gw.mcp);
        cleanup();

        const byPath = renderGatewayUrl(`${gw.mcp}/${slug}?x=1`, { isCloud });
        await waitFor(() => expect(byPath.pathname).toBe(gw.mcp));
        expect(byPath.search).toBe("?x=1");
        expect(document.body.textContent).not.toMatch(PLACEHOLDER);
      },
    );

    it("opens Integrations on its own URL", async () => {
      const location = renderGatewayUrl(gw.integrations, { isCloud });

      expect(
        await screen.findByRole(
          "heading",
          { level: 1, name: "Integrations" },
          // The first render loads the lazy page.
          { timeout: 15000 },
        ),
      ).toBeInTheDocument();
      expect(location.pathname).toBe(gw.integrations);
      expect(document.body.textContent).not.toMatch(PLACEHOLDER);
    });
  },
);

describe("gateway console rail", () => {
  const items = gatewayRailGroups().flatMap((group) => group.items);
  const titles = items.map((item) => item.title);
  const hiddenUrls = HIDDEN_PAGE_URLS.map(([url]) => url);

  it.each(HIDDEN_RAIL_TITLES)(
    "has no %s entry, in any letter case",
    (title) => {
      expect(titles.map((t) => t.toLowerCase())).not.toContain(
        title.toLowerCase(),
      );
    },
  );

  it("keeps Runs, the catalog and Gateway settings", () => {
    expect(items.find((item) => item.title === "Runs").path).toBe(gw.runs);
    expect(items.find((item) => item.title === "Catalog").path).toBe(gw.models);
    expect(items.find((item) => item.title === "Gateway settings").path).toBe(
      gw.settings,
    );
  });

  it.each(MODES)("links every entry to a routed page on %s", (_, isCloud) => {
    const routes = dashboardRoutes(null, null, { isCloud });
    for (const item of items) {
      expect(hiddenUrls, item.path).not.toContain(item.path);
      expect(matchRoutes(routes, item.path), item.path).not.toBeNull();
    }
  });
});

describe("gateway console endpoint keys", () => {
  const keys = endpoints.gateway;

  it("registers every console endpoint with its Django URL", () => {
    expect({
      managedCredits: keys.managedCredits,
      managedCreditEntries: keys.managedCreditEntries,
      managedCreditStatement: keys.managedCreditStatement,
      managedCreditCheckout: keys.managedCreditCheckout,
      managedCreditCheckoutStatus: keys.managedCreditCheckoutStatus("cs_1"),
      managedCreditSettings: keys.managedCreditSettings,
      managedCreditGrant: keys.managedCreditGrant,
      managedCreditAdjust: keys.managedCreditAdjust,
      managedCreditRefund: keys.managedCreditRefund,
      managedCreditReconciliation: keys.managedCreditReconciliation,
      managedCatalogEndpoints: keys.managedCatalogEndpoints,
      managedCatalogAccess: keys.managedCatalogAccess,
      managedCatalogStats: keys.managedCatalogStats,
      managedCatalogHistory: keys.managedCatalogHistory,
      managedCatalogRefresh: keys.managedCatalogRefresh,
      managedCatalogReplay: keys.managedCatalogReplay,
      capabilities: keys.capabilities,
      policyRollout: keys.policyRollout,
      policyRolloutStage: keys.policyRolloutStage,
      policyRolloutPromote: keys.policyRolloutPromote,
      policyRolloutAbort: keys.policyRolloutAbort,
      analyticsBillingSummary: keys.analyticsBillingSummary,
      analyticsBillingMargin: keys.analyticsBillingMargin,
      analyticsAgents: keys.analyticsAgents,
      analyticsFailures: keys.analyticsFailures,
      analyticsHealthSeries: keys.analyticsHealthSeries,
      analyticsReport: keys.analyticsReport,
      runs: keys.runs,
      runDetail: keys.runDetail("run-42"),
      providerCredentialRotate: keys.providerCredentialRotate("cred-7"),
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
    expect(keys.runDetail("team/run 1")).toBe("/agentcc/runs/team%2Frun%201/");
    expect(keys.managedCreditCheckoutStatus("cs/1")).toBe(
      "/agentcc/managed-credits/checkout/cs%2F1/",
    );
  });

  it("refuses to build a URL without its path parameter", () => {
    expect(() => keys.runDetail("")).toThrow(/run_id/);
    expect(() => keys.managedCreditCheckoutStatus(undefined)).toThrow(
      /session_id/,
    );
  });
});
