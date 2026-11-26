import { describe, it, expect } from "vitest";

import { mapCallRow } from "src/api/simulate-environments/runCalls";

import {
  heldBackTally,
  mustPassScenarioKeys,
  scenarioOutcomes,
  scenarioSetOf,
  trialsText,
} from "../scenarioOutcomes";

const task = (key, status, name = key) => ({
  sourceScenarioKey: key,
  sourceScenario: name,
  harnessOutcomeStatus: status,
});

// A verdict whose must-pass check counted scenario b, in the engine's shape.
const BROKE_B = {
  must_pass_regressions: ["b: polite"],
  must_pass_within_noise: [],
};

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
      scenarioOutcomes(before, after, BROKE_B).map((r) => [r.key, r.outcome]),
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
    // One failed trial is a drop; only the verdict says it broke.
    const counted = scenarioOutcomes(
      [task("x", "passed")],
      [task("x", "passed"), task("x", "failed")],
      { must_pass_regressions: ["x: polite"], must_pass_within_noise: [] },
    );
    expect(counted[0].outcome).toBe("broke");
  });

  it("never calls a drop Broke when the verdict counted it within noise", () => {
    const before = ["passed", "passed", "passed"].map((s) => task("x", s));
    const after = ["passed", "passed", "failed", "passed", "passed"].map((s) =>
      task("x", s),
    );
    const outcome = (verdict) =>
      scenarioOutcomes(before, after, verdict)[0].outcome;
    expect(
      outcome({
        must_pass_regressions: [],
        must_pass_within_noise: ["x: polite"],
      }),
    ).toBe("noise");
    // A drop the verdict named nowhere (a must-pass check that never ran)
    // reads as what it was, not as Broke.
    expect(
      outcome({ must_pass_regressions: [], must_pass_within_noise: [] }),
    ).toBe("dropped");
    expect(outcome(null)).toBe("dropped");
    // A scenario named for one check is matched by its key, never a prefix of it.
    expect(
      outcome({
        must_pass_regressions: ["x-2: polite"],
        must_pass_within_noise: [],
      }),
    ).toBe("dropped");
    expect(
      outcome({
        must_pass_regressions: ["x: polite"],
        must_pass_within_noise: [],
      }),
    ).toBe("broke");
  });

  it("keeps a scenario with nothing measured as Not measured", () => {
    const rows = scenarioOutcomes(
      [task("a", "passed"), task("b", "failed"), task("c", "unmeasured")],
      [
        task("a", "unmeasured"),
        task("a", "unmeasured"),
        task("b", "passed"),
        task("c", "passed"),
      ],
    );
    expect(rows.map((r) => [r.key, r.outcome, r.trials])).toEqual([
      ["b", "fixed", "1/1 trial"],
      ["a", "not-measured", "2 trials not measured"],
      ["c", "not-measured", "not measured before the change"],
    ]);
    expect(rows[1]).toMatchObject({ before: "passed", after: null });
  });

  it("says how many trials were not measured, with no slash when some were not", () => {
    expect(trialsText({ passed: 4, of: 5, ran: 5 })).toBe("4/5 trials");
    expect(trialsText({ passed: 4, of: 4, ran: 5 })).toBe(
      "4 of 4 measured, 1 not measured",
    );
    expect(trialsText({ passed: 0, of: 0, ran: 1 })).toBe(
      "1 trial not measured",
    );
  });

  it("counts each scenario once, however many of its checks the verdict names", () => {
    expect([
      ...mustPassScenarioKeys(["a: polite", "a: refunded", "b: polite"]),
    ]).toEqual(["a", "b"]);
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
        task("e", "unmeasured"),
      ],
      BROKE_B,
    );
    expect(rows.map((r) => r.outcome)).toEqual([
      "broke",
      "fixed",
      "still-failing",
      "same",
      "not-measured",
    ]);
  });

  it("keeps every call of a scenario for the compare, and counts the measured trials", () => {
    const [row] = scenarioOutcomes(
      [task("a", "failed"), task("a", "unmeasured")],
      [task("a", "passed"), task("a", "failed"), task("a", "unmeasured")],
    );
    expect(row.beforeCalls).toHaveLength(2);
    expect(row.afterCalls).toHaveLength(3);
    expect(row.afterTrials).toEqual({ passed: 1, of: 2, ran: 3 });
    expect(row.trials).toBe("1 of 2 measured, 1 not measured");
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
