import { describe, expect, it } from "vitest";

import { dashboardRoutes } from "../dashboard";

function findChildRoute(route, path) {
  return route?.children?.find((child) => child.path === path);
}

describe("dashboard route registry", () => {
  it("keeps the model list/detail route family reachable", () => {
    const [dashboardRoute] = dashboardRoutes(null, null, { isOSS: false });
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
