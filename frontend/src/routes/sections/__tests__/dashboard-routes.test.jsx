import { Navigate, matchRoutes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import HarnessToEnvironment from "../../components/harness-to-environment";
import { dashboardRoutes } from "../dashboard";

function findChildRoute(route, path) {
  return route?.children?.find((child) => child.path === path);
}

describe("dashboard route registry", () => {
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

// Each Prebuilt template URL and the route that must serve it, so
// "templates" is never read as an :envId.
const TEMPLATE_ROUTES = [
  [`${SIMULATE}/environments/templates`, "environments/templates"],
  [
    `${SIMULATE}/environments/templates/template-1`,
    "environments/templates/:templateId",
  ],
];

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

  it("an old harness job link opens that environment", () => {
    const route = leafRoute(user, `${SIMULATE}/harness/job-1`);
    expect(route.element.type).toBe(HarnessToEnvironment);
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
