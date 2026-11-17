import { describe, expect, it } from "vitest";
import { compareWithPlan, hasResults } from "../compareWithModel";

const run = (id, done) => ({
  run_id: id,
  progress: { done, total: 20, scored: done },
});

describe("Compare with… (L7c g6)", () => {
  it("never makes a waiting run the baseline; the run you came from is the candidate", () => {
    const runs = [run("6A", 0), run("7A", 20), run("5A", 20)];
    const plan = compareWithPlan(["6A", "7A"], "5A", runs, null);
    expect(plan.baselineId).toBe("7A");
    expect(plan.cand).toBe("5A");
    expect(plan.runIds).toEqual(["7A", "6A", "5A"]);
  });
  it("prefers the pinned baseline when picked and it has results", () => {
    const runs = [run("1A", 238), run("7A", 20), run("5A", 20)];
    expect(compareWithPlan(["7A", "1A"], "5A", runs, "1A").baselineId).toBe(
      "1A",
    );
  });
  it("no picked run with results: no baseline (absolute)", () => {
    expect(
      compareWithPlan(["6A"], "5A", [run("6A", 0)], null).baselineId,
    ).toBeNull();
    expect(hasResults(run("6A", 0))).toBe(false);
  });
});
