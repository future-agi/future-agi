import { describe, expect, it } from "vitest";

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
