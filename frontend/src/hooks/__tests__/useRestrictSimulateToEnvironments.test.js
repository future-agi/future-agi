import { describe, expect, it } from "vitest";

import { isHiddenFromRestrictedOrgs } from "../useRestrictSimulateToEnvironments";

describe("isHiddenFromRestrictedOrgs", () => {
  it.each([
    "/dashboard/simulate/agent-definitions/a-1",
    "/dashboard/simulate/agent-definitions/create-new-agent-definition",
    "/dashboard/simulate/scenarios",
    "/dashboard/simulate/scenarios/s-1?tab=data",
    "/dashboard/simulate/test",
    "/dashboard/simulate/test/t-1/e-1/call-details",
  ])("hides %s", (path) => {
    expect(isHiddenFromRestrictedOrgs(path)).toBe(true);
  });

  it.each([
    // The list redirects to Connected agents for every org.
    "/dashboard/simulate/agent-definitions",
    "/dashboard/simulate/environments",
    "/dashboard/simulate/testing",
    "/dashboard/dashboards/d-1",
    "",
    undefined,
  ])("keeps %s", (path) => {
    expect(isHiddenFromRestrictedOrgs(path)).toBe(false);
  });
});
