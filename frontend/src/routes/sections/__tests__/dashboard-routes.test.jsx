import { execFileSync } from "node:child_process";
import process from "node:process";
import { Suspense } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import MetricsGate from "src/sections/derived-metrics/MetricsGate";
import {
  Navigate,
  RouterProvider,
  createMemoryRouter,
  matchRoutes,
} from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { paths } from "../../paths";

import CapabilityGate from "src/components/capability-gate";
import HarnessToEnvironment from "../../components/harness-to-environment";
import LegacyMetricsRedirect from "../../components/legacy_metrics_redirect";
import { dashboardRoutes } from "../dashboard";

function findChildRoute(route, path) {
  return route?.children?.find((child) => child.path === path);
}

describe("dashboard route registry", () => {
  it("keeps the retained dashboard and metric list imports resolvable", () => {
    // Bundle in Node, not jsdom, so esbuild uses one typed-array realm. Keep
    // packages external: this checks local removal ordering, not vendor code.
    const output = execFileSync(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        `import { build } from "esbuild";
        for (const entryPoint of [
          "src/sections/dashboards/DerivedMetricCardContext.jsx",
          "src/sections/derived-metrics/MetricList.jsx",
        ]) {
          await build({
            entryPoints: [entryPoint],
            bundle: true,
            write: false,
            packages: "external",
            logLevel: "silent",
          });
        }
        process.stdout.write("2 local dependency graphs resolved");`,
      ],
      {
        cwd: process.cwd(),
        encoding: "utf8",
        timeout: 10000,
      },
    );
    expect(output).toBe("2 local dependency graphs resolved");
  });

  it("keeps the model list/detail route family reachable", () => {
    const [dashboardRoute] = dashboardRoutes(null, null, { isCloud: true });
    const modelsRoute = findChildRoute(dashboardRoute, "models");
    const detailRoute = findChildRoute(modelsRoute, ":id");
    const datasetsRoute = findChildRoute(detailRoute, "datasets");
    const optimizeRoute = findChildRoute(detailRoute, "optimize");

    expect(modelsRoute).toBeTruthy();
    expect(modelsRoute.children.some((child) => child.index)).toBe(true);
    expect(detailRoute).toBeTruthy();
    expect(detailRoute.children.some((child) => child.index)).toBe(true);
    expect(findChildRoute(detailRoute, "performance")).toBeTruthy();
    expect(findChildRoute(detailRoute, "custom-metrics")).toBeTruthy();
    expect(findChildRoute(detailRoute, "report")).toBeTruthy();
    expect(findChildRoute(detailRoute, "config")).toBeTruthy();
    expect(datasetsRoute?.children.some((child) => child.index)).toBe(true);
    expect(findChildRoute(datasetsRoute, ":dataset")).toBeTruthy();
    expect(optimizeRoute?.children.some((child) => child.index)).toBe(true);
    expect(findChildRoute(optimizeRoute, ":optimizeId")).toBeTruthy();
  });

  // Enterprise, as Error Feed: without the capability the route shows the
  // upgrade gate instead of the page.
  it.each([
    ["ask", "instant_eval"],
    ["insights", "insights"],
  ])("gates the observe %s route on %s", (path, feature) => {
    const [dashboardRoute] = dashboardRoutes(null, null, { isCloud: false });
    const observeRoute = findChildRoute(dashboardRoute, "observe/:observeId");
    const element = findChildRoute(observeRoute, path)?.element;
    expect(element?.type).toBe(CapabilityGate);
    expect(element.props.feature).toBe(feature);
  });
});

// TH-8602: orgs created after the cutoff see only Environments in Simulate.
const RESTRICTED = {
  organization_role: "Owner",
  restrict_simulate_to_environments: true,
};
const UNRESTRICTED = {
  organization_role: "Owner",
  restrict_simulate_to_environments: false,
};
// A backend from before the flag existed leaves it out.
const WITHOUT_FLAG = { organization_role: "Owner" };

// The 404 catch-all lives beside the dashboard routes (routes/sections/index.jsx).
const CATCH_ALL = { path: "*" };

function leafRoute(user, url) {
  const routes = [...dashboardRoutes(user, null, { isCloud: true }), CATCH_ALL];
  const matches = matchRoutes(routes, url);
  return matches?.[matches.length - 1]?.route;
}

function redirectTarget(route) {
  return route?.element?.type === Navigate ? route.element.props.to : null;
}

const SIMULATE = "/dashboard/simulate";

const HIDDEN_SIMULATE_URLS = [
  // Agent Definition (its list link opens Connected agents for every org)
  `${SIMULATE}/agent-definitions/create-new-agent-definition`,
  `${SIMULATE}/agent-definitions/agent-1`,
  // Scenarios
  `${SIMULATE}/scenarios`,
  `${SIMULATE}/scenarios/create`,
  `${SIMULATE}/scenarios/scenario-1`,
  // Run Simulation
  `${SIMULATE}/test`,
  `${SIMULATE}/test/test-1`,
  `${SIMULATE}/test/test-1/runs`,
  `${SIMULATE}/test/test-1/call-logs`,
  `${SIMULATE}/test/test-1/analytics`,
  `${SIMULATE}/test/test-1/exec-1`,
  `${SIMULATE}/test/test-1/exec-1/call-details`,
  `${SIMULATE}/test/test-1/exec-1/performance`,
  `${SIMULATE}/test/test-1/exec-1/analytics`,
  `${SIMULATE}/test/test-1/exec-1/optimization_runs`,
  `${SIMULATE}/test/test-1/exec-1/opt-1`,
  `${SIMULATE}/test/test-1/exec-1/opt-1/trial-1`,
];

const ENVIRONMENT_URLS = [
  `${SIMULATE}/environments`,
  `${SIMULATE}/environments/env-1`,
  `${SIMULATE}/environments/env-1/runs/test-1/exec-1`,
  `${SIMULATE}/environments/env-1/runs/test-1/exec-1/call-details`,
  `${SIMULATE}/environments/env-1/runs/test-1/exec-1/performance`,
  `${SIMULATE}/environments/env-1/runs/test-1/exec-1/analytics`,
  `${SIMULATE}/environments/env-1/runs/test-1/exec-1/optimization_runs`,
];

// The Prebuilt list has its own route, so "templates" is never read as an
// :envId. A template has no page of its own: prebuilt environments are adopted
// from released environments, so a template URL is a 404, not an environment.
const TEMPLATE_ROUTES = [
  [`${SIMULATE}/environments/templates`, "environments/templates"],
];
const TEMPLATE_DETAIL_URL = `${SIMULATE}/environments/templates/template-1`;

const ALWAYS_REDIRECTED = [
  [SIMULATE, `${SIMULATE}/environments`],
  [`${SIMULATE}/harness`, `${SIMULATE}/environments`],
  [`${SIMULATE}/environments/build`, `${SIMULATE}/environments?tab=build`],
  [`${SIMULATE}/harness/new`, `${SIMULATE}/environments?tab=build`],
  [`${SIMULATE}/harness/job-1/old`, `${SIMULATE}/environments`],
  [`${SIMULATE}/simulator-agent`, `${SIMULATE}/environments`],
  [`${SIMULATE}/personas`, `${SIMULATE}/environments?tab=personas`],
  [`${SIMULATE}/agent-definitions`, "/dashboard/agents/connected"],
];

describe("Simulate routes for an org restricted to Environments", () => {
  it.each(HIDDEN_SIMULATE_URLS)("%s falls through to the 404", (url) => {
    expect(leafRoute(RESTRICTED, url)).toBe(CATCH_ALL);
  });

  it.each(ENVIRONMENT_URLS)("%s still renders", (url) => {
    expect(leafRoute(RESTRICTED, url)).not.toBe(CATCH_ALL);
  });
});

describe.each([
  ["an unrestricted org", UNRESTRICTED],
  ["a user-info without the flag", WITHOUT_FLAG],
])("Simulate routes for %s", (_label, user) => {
  it.each([...HIDDEN_SIMULATE_URLS, ...ENVIRONMENT_URLS])(
    "%s renders",
    (url) => {
      expect(leafRoute(user, url)).not.toBe(CATCH_ALL);
    },
  );
});

describe.each([
  ["restricted", RESTRICTED],
  ["unrestricted", UNRESTRICTED],
])("Simulate routes for every org (%s)", (_label, user) => {
  it.each(ALWAYS_REDIRECTED)("%s redirects to %s", (url, target) => {
    expect(redirectTarget(leafRoute(user, url))).toBe(target);
  });

  it.each(TEMPLATE_ROUTES)("%s opens the %s page", (url, path) => {
    const route = leafRoute(user, url);
    expect(route.path).toBe(path);
    expect(redirectTarget(route)).toBeNull();
  });

  it("a template URL is a 404, not read as an environment", () => {
    expect(leafRoute(user, TEMPLATE_DETAIL_URL)).toBe(CATCH_ALL);
  });

  it("an old harness job link opens that environment", () => {
    const route = leafRoute(user, `${SIMULATE}/harness/job-1`);
    expect(route.element.type).toBe(HarnessToEnvironment);
  });
});

describe("existing Evals and Tasks routes", () => {
  it.each([
    ["/dashboard/evaluations/create", "create"],
    ["/dashboard/evaluations/create/draft-id", "create/:draftId"],
    ["/dashboard/evaluations/existing-evaluator", ":evalId"],
    ["/dashboard/evaluations/usage", "usage"],
    ["/dashboard/evaluations/groups", "groups"],
    ["/dashboard/evaluations/groups/group-id", "groups/:id"],
    [
      "/dashboard/tasks/create?projectId=p&returnTo=%2Fdashboard%2Fobserve",
      "create",
    ],
    ["/dashboard/tasks/task-id", ":taskId"],
  ])("%s retains its static/detail destination", (url, path) => {
    expect(leafRoute(WITHOUT_FLAG, url).path).toBe(path);
  });
  it("no longer mounts Metrics under Evals", () => {
    const [dashboardRoute] = dashboardRoutes(WITHOUT_FLAG, null, {
      isCloud: true,
    });
    const evals = findChildRoute(dashboardRoute, "evaluations");
    expect(
      evals.children.some((route) => route.path?.startsWith("metrics")),
    ).toBe(false);
  });
});

// Exercise the production route entries and lazy page imports without mounting
// the unrelated authenticated dashboard shell and its network providers.
let metricsRouter;
let metricsQueryClient;
afterEach(() => {
  cleanup();
  metricsRouter?.dispose();
  metricsRouter = undefined;
  metricsQueryClient?.clear();
  metricsQueryClient = undefined;
});
function renderMetricsRoute(url, capabilities = { metrics_enabled: true }) {
  const [{ path, children }] = dashboardRoutes(WITHOUT_FLAG, null, {
    isCloud: true,
  });
  // Keep the production hook/query/provider path, with an explicit rollout
  // response rather than a global mock that would bypass MetricsGate.
  metricsQueryClient = new QueryClient();
  metricsQueryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: capabilities,
  });
  metricsRouter = createMemoryRouter(
    [
      { path: "/before-metrics", element: <div>Previous page</div> },
      { path: "/404", element: <div>Not found</div> },
      { path, children },
    ],
    { initialEntries: ["/before-metrics", url], initialIndex: 1 },
  );
  render(
    <QueryClientProvider client={metricsQueryClient}>
      <Suspense fallback={<div>Loading page</div>}>
        <RouterProvider router={metricsRouter} />
      </Suspense>
    </QueryClientProvider>,
  );
  return metricsRouter;
}

const METRIC_ID = "44444444-4444-4444-8444-444444444444";

describe("Metrics routes in Observe", () => {
  it("defines canonical list, create, detail and edit path helpers", () => {
    expect(paths.dashboard.metrics.root).toBe("/dashboard/metrics");
    expect(paths.dashboard.metrics.new).toBe("/dashboard/metrics/new");
    expect(paths.dashboard.metrics.detail(METRIC_ID)).toBe(
      `/dashboard/metrics/${METRIC_ID}`,
    );
    expect(paths.dashboard.metrics.edit(METRIC_ID)).toBe(
      `/dashboard/metrics/${METRIC_ID}/edit`,
    );
    expect(paths.dashboard).not.toHaveProperty("evalMetrics");
  });

  it.each([
    ["/dashboard/metrics", undefined, "Metrics", undefined],
    ["/dashboard/metrics/new", "new", "New metric", undefined],
    [`/dashboard/metrics/${METRIC_ID}`, ":metricId", "Metric", METRIC_ID],
    [
      `/dashboard/metrics/${METRIC_ID}?tab=overview`,
      ":metricId",
      "Metric",
      METRIC_ID,
    ],
    [
      `/dashboard/metrics/${METRIC_ID}?tab=history`,
      ":metricId",
      "Metric",
      METRIC_ID,
    ],
    [
      `/dashboard/metrics/${METRIC_ID}?tab=definition`,
      ":metricId",
      "Metric",
      METRIC_ID,
    ],
    [
      `/dashboard/metrics/${METRIC_ID}/edit`,
      ":metricId/edit",
      "Edit metric",
      METRIC_ID,
    ],
  ])("%s renders its own page", async (url, path, heading, metricId) => {
    const route = leafRoute(WITHOUT_FLAG, url);
    expect(route.path).toBe(path);
    if (!path) expect(route.index).toBe(true);
    const router = renderMetricsRoute(url);
    expect(
      await screen.findByRole("heading", { name: heading }),
    ).toBeInTheDocument();
    expect(router.state.matches.at(-1).params.metricId).toBe(metricId);
    expect(router.state.location.pathname + router.state.location.search).toBe(
      url,
    );
  });

  it("redirects the case-insensitive legacy route accepted by the router", async () => {
    const url = `/DASHBOARD/Derived-Metrics/${METRIC_ID}?metric=kept#x`;
    const route = leafRoute(WITHOUT_FLAG, url);
    expect(route.element.type).toBe(MetricsGate);
    expect(route.element.props.children.type).toBe(LegacyMetricsRedirect);
    const router = renderMetricsRoute(url);
    await waitFor(() =>
      expect(router.state.location).toMatchObject({
        pathname: `/dashboard/metrics/${METRIC_ID}`,
        search: "?metric=kept",
        hash: "#x",
      }),
    );
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it.each([
    "",
    "/",
    "/new",
    `/${METRIC_ID}`,
    `/${METRIC_ID}/edit`,
    "/metric%20id/edit",
  ])(
    "legacy suffix '%s' replace-redirects, preserving search and hash",
    async (suffix) => {
      const search = `?metric=${METRIC_ID}&project=a%2Fb&project=second&tab=history&empty=`;
      const hash = "#x";
      const legacyUrl = `/dashboard/derived-metrics${suffix}${search}${hash}`;
      const route = leafRoute(WITHOUT_FLAG, legacyUrl);
      expect(route.element.type).toBe(MetricsGate);
      expect(route.element.props.children.type).toBe(LegacyMetricsRedirect);
      const router = renderMetricsRoute(legacyUrl);
      await waitFor(() =>
        expect(router.state.location).toMatchObject({
          pathname: `/dashboard/metrics${suffix}`,
          search,
          hash,
        }),
      );
      expect(router.state.historyAction).toBe("REPLACE");
      await act(async () => router.navigate(-1));
      expect(await screen.findByText("Previous page")).toBeInTheDocument();
      expect(router.state.location.pathname).toBe("/before-metrics");
    },
  );
});

describe.each([
  ["disabled", { metrics_enabled: false }],
  ["missing from an older capability response", {}],
])("Metrics routes when rollout is %s", (_label, capabilities) => {
  it.each([
    "/dashboard/metrics",
    "/dashboard/metrics/new",
    `/dashboard/metrics/${METRIC_ID}?tab=history`,
    `/dashboard/metrics/${METRIC_ID}/edit`,
    "/dashboard/derived-metrics",
    "/dashboard/derived-metrics/",
    "/dashboard/derived-metrics/new",
    `/dashboard/derived-metrics/${METRIC_ID}?metric=kept#x`,
    `/dashboard/derived-metrics/${METRIC_ID}/edit`,
    `/DASHBOARD/Derived-Metrics/${METRIC_ID}?metric=kept#x`,
  ])("%s replace-navigates to not found", async (url) => {
    const router = renderMetricsRoute(url, capabilities);
    expect(await screen.findByText("Not found")).toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(router.state.location).toMatchObject({
      pathname: "/404",
      search: "",
      hash: "",
    });
    expect(router.state.historyAction).toBe("REPLACE");
    await act(async () => router.navigate(-1));
    expect(await screen.findByText("Previous page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/before-metrics");
  });
});

describe("old Agent Definition link", () => {
  it("opens Connected agents, and keeps the old agent pages under it", () => {
    const [dashboardRoute] = dashboardRoutes(null, null, { isCloud: true });
    const simulate = findChildRoute(dashboardRoute, "simulate");
    const list = findChildRoute(simulate, "agent-definitions");
    expect(list.element.props.to).toBe("/dashboard/agents/connected");
    expect(list.element.props.replace).toBe(true);
    expect(
      findChildRoute(simulate, "agent-definitions/:agentDefinitionId"),
    ).toBeTruthy();
  });
});

describe("New agent", () => {
  it("opens the connect page under Agents, outside the tabs", () => {
    const [dashboardRoute] = dashboardRoutes(null, null, { isCloud: true });
    const agents = findChildRoute(dashboardRoute, "agents");
    expect(findChildRoute(agents, "connect")).toBeTruthy();
    expect(findChildRoute(agents.children[0], "connect")).toBeUndefined();
  });
});
