// Test a fix: when one of the two runs has nothing to count (no evaluation
// with a Pass/fail result), its side has no pass@1 or pass^k, so the banner's
// pass^k chip shows `—` for it and keeps its click.
import { describe, expect, it } from "vitest";
import { passKChip, summarizeIteration } from "../iterateSummary";

const NONE = "run-none";
const PASSES = "run-passes";

// Two rows of two tries. NONE errored on every try; PASSES passed every try.
const matrixOf = (columns) => ({
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    cells: {
      [NONE]: { trials: ["error", "error"] },
      [PASSES]: { trials: [true, true] },
    },
  })),
  ...(columns ? { columns } : {}),
});
const COUNTS = {
  [NONE]: { trials: 2, pass_fail_evals: 0 },
  [PASSES]: { trials: 2, pass_fail_evals: 1 },
};
const NO_FIGURE = { pass: 0, n: 0, rate: null };

describe("a base run with nothing to count", () => {
  const s = summarizeIteration(matrixOf(COUNTS), NONE, PASSES);

  it("has no pass@1 or pass^k", () => {
    expect(s.pass1.from).toEqual(NO_FIGURE);
    expect(s.passK.from).toEqual(NO_FIGURE);
  });

  it("gives the banner a pass^k move with — for it", () => {
    expect(passKChip(s)).toBe("pass^2 — → 100%");
  });

  it("leaves the other side's figures as they were", () => {
    // Green at the base.
    expect(s.pass1.to).toMatchObject({ pass: 4, n: 4, rate: 1 });
    expect(s.passK.to).toMatchObject({ pass: 2, n: 2, rate: 1 });
  });
});

describe("a new run with nothing to count", () => {
  const s = summarizeIteration(matrixOf(COUNTS), PASSES, NONE);

  it("has no pass@1 or pass^k", () => {
    expect(s.pass1.to).toEqual(NO_FIGURE);
    expect(s.passK.to).toEqual(NO_FIGURE);
  });

  it("gives the banner a pass^k move with — for it", () => {
    expect(passKChip(s)).toBe("pass^2 100% → —");
  });
});

describe("two runs when the matrix does not say", () => {
  it("keeps the pass^k move", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the figure, as before.
    const s = summarizeIteration(matrixOf(null), NONE, PASSES);
    expect(passKChip(s)).toBe("pass^2 0% → 100%");
  });
});
