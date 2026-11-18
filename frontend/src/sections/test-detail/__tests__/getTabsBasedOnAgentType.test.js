import { describe, it, expect } from "vitest";
import { getTabsBasedOnAgentType } from "../common";
import { AGENT_TYPES } from "src/sections/agents/constants";

// Characterization test: without `basePath` the tab hrefs must stay the exact
// legacy absolute `/dashboard/simulate/test/...` strings so the standalone
// product route is byte-for-byte unchanged after the basePath refactor.
describe("getTabsBasedOnAgentType", () => {
  it("returns the legacy absolute paths when no basePath is passed (voice)", () => {
    const tabs = getTabsBasedOnAgentType({
      agentType: AGENT_TYPES.VOICE,
      testId: "t1",
      executionId: "e1",
    });

    expect(tabs.map((t) => ({ id: t.id, title: t.title, path: t.path }))).toEqual(
      [
        {
          id: "runs",
          title: "Call Details",
          path: "/dashboard/simulate/test/t1/e1/call-details",
        },
        {
          id: "analytics",
          title: "Analytics",
          path: "/dashboard/simulate/test/t1/e1/analytics",
        },
        {
          id: "optimization_runs",
          title: "Optimization Runs",
          path: "/dashboard/simulate/test/t1/e1/optimization_runs",
        },
      ],
    );
  });

  it("uses the Chat Details title for chat agents but keeps the legacy paths", () => {
    const tabs = getTabsBasedOnAgentType({
      agentType: AGENT_TYPES.CHAT,
      testId: "t1",
      executionId: "e1",
    });

    expect(tabs[0].title).toBe("Chat Details");
    expect(tabs[0].path).toBe("/dashboard/simulate/test/t1/e1/call-details");
  });

  it("builds the tab hrefs from basePath when one is passed", () => {
    const tabs = getTabsBasedOnAgentType({
      agentType: AGENT_TYPES.VOICE,
      testId: "t1",
      executionId: "e1",
      basePath: "/dashboard/simulate/environments/env1/runs/t1/e1",
    });

    expect(tabs.map((t) => t.path)).toEqual([
      "/dashboard/simulate/environments/env1/runs/t1/e1/call-details",
      "/dashboard/simulate/environments/env1/runs/t1/e1/analytics",
      "/dashboard/simulate/environments/env1/runs/t1/e1/optimization_runs",
    ]);
  });
});
