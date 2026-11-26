import { describe, it, expect } from "vitest";

import { runnableScenarioKey } from "../scenarioSelection";

describe("runnableScenarioKey", () => {
  it("has no key for an added row", () => {
    expect(
      runnableScenarioKey({ scenario_key: "c", draft_change: "added" }),
    ).toBeNull();
    expect(runnableScenarioKey({ scenario_key: "a" })).toBe("a");
  });
});
