// The Workbench's "Where the gap is": a run with nothing to count (no
// evaluation with a Pass/fail result) has no pass@1 on any slice, so no
// slice line shows it as `0/8` and no slice has a gap, whichever side it is
// on. Its slices stay, by size, so each can still be acted on.
import { describe, expect, it } from "vitest";
import { gapSlices } from "../runStats";

const NONE = "run-none";
const BOTH = "run-both";
const SPLIT = "run-split";

// Eight rows of two tries, four per category. NONE errored on every try;
// BOTH passed every try; SPLIT failed every refund try and passed the rest.
const matrixOf = (columns) => ({
  rows: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => {
    const refund = n <= 4;
    return {
      row_id: `row-${n}`,
      n,
      group_values: { category: refund ? "refund" : "status" },
      cells: {
        [NONE]: { trials: ["error", "error"] },
        [BOTH]: { trials: [true, true] },
        [SPLIT]: { trials: refund ? [false, false] : [true, true] },
      },
    };
  }),
  ...(columns ? { columns } : {}),
});
const COUNTS = {
  [NONE]: { trials: 2, pass_fail_evals: 0 },
  [BOTH]: { trials: 2, pass_fail_evals: 1 },
  [SPLIT]: { trials: 2, pass_fail_evals: 1 },
};
const KEYS = { keys: ["category"] };
const shown = (g) => g.map((s) => [s.value, s.delta_pts, s.from, s.to]);

describe("the gap with a run that has nothing to count", () => {
  it("has no figure for it and no gap when it is the baseline", () => {
    const g = gapSlices(matrixOf(COUNTS), NONE, BOTH, KEYS);
    expect(shown(g)).toEqual([
      ["refund", null, null, { pass: 8, n: 8 }],
      ["status", null, null, { pass: 8, n: 8 }],
    ]);
    expect(g[0].uncounted).toEqual({ from: true, to: false });
    expect(g[0].row_ids).toEqual(["row-1", "row-2", "row-3", "row-4"]);
  });

  it("has no figure for it and no gap when it is the candidate", () => {
    const g = gapSlices(matrixOf(COUNTS), BOTH, NONE, KEYS);
    expect(shown(g)).toEqual([
      ["refund", null, { pass: 8, n: 8 }, null],
      ["status", null, { pass: 8, n: 8 }, null],
    ]);
    expect(g[0].uncounted).toEqual({ from: false, to: true });
  });
});

describe("the gap between two runs with figures", () => {
  it("keeps its slices", () => {
    // Green at the base.
    const g = gapSlices(matrixOf(COUNTS), SPLIT, BOTH, KEYS);
    expect(shown(g)).toEqual([
      ["refund", 100, { pass: 0, n: 8 }, { pass: 8, n: 8 }],
      ["status", 0, { pass: 8, n: 8 }, { pass: 8, n: 8 }],
    ]);
    expect(g[0].uncounted).toBeUndefined();
  });

  it("keeps the slices of a run the matrix does not say about", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the figure, as before.
    const g = gapSlices(matrixOf(null), NONE, BOTH, KEYS);
    expect(g.map((s) => s.from)).toEqual([
      { pass: 0, n: 8 },
      { pass: 0, n: 8 },
    ]);
  });
});
