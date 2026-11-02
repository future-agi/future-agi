import { describe, it, expect } from "vitest";

import { heldBackTally, scenarioOutcomes } from "../scenarioOutcomes";

const task = (key, status, name = key) => ({
  sourceScenarioKey: key,
  sourceScenario: name,
  harnessOutcomeStatus: status,
});

describe("scenarioOutcomes", () => {
  it("classifies each scenario before and after the change", () => {
    const before = [
      task("a", "failed"),
      task("b", "passed"),
      task("c", "failed"),
      task("d", "passed"),
    ];
    const after = [
      task("a", "passed"),
      task("b", "failed"),
      task("c", "failed"),
      task("d", "passed"),
    ];
    const byKey = Object.fromEntries(
      scenarioOutcomes(before, after).map((r) => [r.key, r.outcome]),
    );
    expect(byKey).toEqual({
      a: "fixed",
      b: "broke",
      c: "still-failing",
      d: "same",
    });
  });

  it("needs every measured trial to pass, and ignores unmeasured ones", () => {
    const before = [task("a", "failed"), task("a", "unmeasured")];
    const after = [
      task("a", "passed"),
      task("a", "unmeasured"),
      task("a", "passed"),
    ];
    expect(scenarioOutcomes(before, after)[0]).toMatchObject({
      before: "failed",
      after: "passed",
      outcome: "fixed",
    });
    const mixed = scenarioOutcomes(
      [task("x", "passed")],
      [task("x", "passed"), task("x", "failed")],
    );
    expect(mixed[0].outcome).toBe("broke");
  });

  it("orders fixed, broke, still failing, then unchanged", () => {
    const rows = scenarioOutcomes(
      [
        task("d", "passed"),
        task("c", "failed"),
        task("b", "passed"),
        task("a", "failed"),
      ],
      [
        task("d", "passed"),
        task("c", "failed"),
        task("b", "failed"),
        task("a", "passed"),
      ],
    );
    expect(rows.map((r) => r.outcome)).toEqual([
      "fixed",
      "broke",
      "still-failing",
      "same",
    ]);
  });

  it("counts a held-back scenario the winner never measured as not fixed", () => {
    const before = [task("a", "failed"), task("b", "failed"), task("c", "passed")];
    const after = [task("a", "passed")];
    expect(heldBackTally(before, after, ["a", "b", "c"])).toEqual({
      fixed: 1,
      failing: 2,
    });
  });
});
