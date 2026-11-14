import { describe, expect, it } from "vitest";

import { WORKBENCH_TAB, visibleWorkbenchTab } from "../workbenchTab";

describe("visibleWorkbenchTab", () => {
  it("opens Playground when an org restricted to Environments asks for Simulation", () => {
    expect(visibleWorkbenchTab(WORKBENCH_TAB.SIMULATION, true)).toBe(
      WORKBENCH_TAB.PLAYGROUND,
    );
  });

  it.each(["Playground", "Evaluation", "Metrics"])(
    "keeps %s for an org restricted to Environments",
    (tab) => {
      expect(visibleWorkbenchTab(tab, true)).toBe(tab);
    },
  );

  it("keeps Simulation for other orgs", () => {
    expect(visibleWorkbenchTab(WORKBENCH_TAB.SIMULATION, false)).toBe(
      WORKBENCH_TAB.SIMULATION,
    );
  });
});
