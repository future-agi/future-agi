import { describe, it, expect } from "vitest";

import { mapCallRow } from "src/api/simulate-environments/runCalls";

import {
  heldBackTally,
  scenarioOutcomes,
  scenarioSetOf,
} from "../scenarioOutcomes";

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

  it("orders broke, fixed, still failing, then unchanged", () => {
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
      "broke",
      "fixed",
      "still-failing",
      "same",
    ]);
  });

  it("keeps every call of a scenario for the compare, and counts the measured trials", () => {
    const [row] = scenarioOutcomes(
      [task("a", "failed"), task("a", "unmeasured")],
      [task("a", "passed"), task("a", "failed"), task("a", "unmeasured")],
    );
    expect(row.beforeCalls).toHaveLength(2);
    expect(row.afterCalls).toHaveLength(3);
    expect(row.afterTrials).toEqual({ passed: 1, of: 2 });
  });

  it("reads the judge's outcome, so a call a platform eval made inconclusive still counts", () => {
    const row = (outcome, harness) =>
      mapCallRow({
        id: `late-cancel-${outcome}`,
        source_scenario_key: "late-cancel",
        trial_index: 1,
        outcome,
        harness_outcome_status: harness,
      });
    expect(
      scenarioOutcomes(
        [row("failed", "failed")],
        [row("inconclusive", "passed")],
      ).map((r) => [r.key, r.outcome]),
    ).toEqual([["late-cancel", "fixed"]]);
  });

  it("puts each scenario in the set the request names, never a guessed must-pass", () => {
    const request = {
      target_scenario_keys: ["t"],
      held_back_scenario_keys: ["h"],
      protected_scenario_keys: ["p"],
    };
    expect(scenarioSetOf(request, "t")).toBe("target");
    expect(scenarioSetOf(request, "h")).toBe("held");
    expect(scenarioSetOf(request, "p")).toBe("protected");
    // The read lists no must-pass scenarios, so any other case is in no set.
    expect(scenarioSetOf(request, "x")).toBeNull();
  });

  it("counts a held-back scenario the winner never measured as not fixed", () => {
    const before = [
      task("a", "failed"),
      task("b", "failed"),
      task("c", "passed"),
    ];
    const after = [task("a", "passed")];
    expect(heldBackTally(before, after, ["a", "b", "c"])).toEqual({
      fixed: 1,
      failing: 2,
    });
  });
});
