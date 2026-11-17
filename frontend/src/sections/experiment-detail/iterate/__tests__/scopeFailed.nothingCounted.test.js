// Test a fix's rerun scope for a run with nothing to count (no evaluation
// with a Pass/fail result): the Failed chip keeps its rows and what it
// reruns, and reads "Failed (—)" with what Success needs as its help line,
// since its errored tries are in no figure. A run with a figure is unchanged.
import { describe, expect, it } from "vitest";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { scopeOptions, scopeSets } from "../iterateModel";

const NONE = "run-none";
const COUNTED = "run-counted";

// Three rows of two tries. NONE errored on every try of rows 1 and 2 and
// has no result on row 3; COUNTED failed one try of row 1.
const matrixOf = (columns) => ({
  rows: [1, 2, 3].map((n) => ({
    row_id: `row-${n}`,
    n,
    label: `T${n}`,
    cells: {
      [NONE]: { trials: n < 3 ? ["error", "error"] : [null, null] },
      [COUNTED]: { trials: n === 1 ? [1, 0] : [1, 1] },
    },
  })),
  columns,
});
const COUNTS = {
  [NONE]: { trials: 2, pass_fail_evals: 0 },
  [COUNTED]: { trials: 2, pass_fail_evals: 1 },
};
const failedOf = (opts) => opts.find((o) => o.type === "failed");

describe("the Failed chip of a run with nothing to count", () => {
  it("keeps its rows and what it reruns", () => {
    // Green at the base: the rows are the errored rows, as before.
    const sets = scopeSets(matrixOf(COUNTS), NONE, {});
    expect(sets.failed).toEqual(["row-1", "row-2"]);
    expect(sets.failedEvery).toEqual(["row-1", "row-2"]);
    expect(failedOf(scopeOptions(sets, { parentLabel: "#1" })).count).toBe(2);
  });

  it("reads Failed (—) with what Success needs", () => {
    const sets = scopeSets(matrixOf(COUNTS), NONE, {});
    expect(failedOf(scopeOptions(sets, { parentLabel: "#1" }))).toMatchObject({
      label: "Failed (—)",
      tip: SUCCESS_NEEDS_PASS_FAIL,
      count: 2,
    });
  });

  it("says only what Success needs when a finding is in scope", () => {
    const sets = scopeSets(matrixOf(COUNTS), NONE, { rows: ["T1"] });
    const opts = scopeOptions(sets, {
      parentLabel: "#1",
      source: "Finding",
      causeLabel: "order_found",
    });
    expect(failedOf(opts).tip).toBe(SUCCESS_NEEDS_PASS_FAIL);
  });
});

describe("the Failed chip of a run with a figure", () => {
  it("reads as before", () => {
    // Green at the base.
    const sets = scopeSets(matrixOf(COUNTS), COUNTED, {});
    expect(failedOf(scopeOptions(sets, { parentLabel: "#2" }))).toMatchObject({
      label: "Failed (1)",
      tip: "1 row has a failing try on #2; 0 fail every try",
      count: 1,
    });
  });

  it("reads as before for a run the matrix does not say about", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are counted, as before.
    const sets = scopeSets(matrixOf({}), NONE, {});
    expect(failedOf(scopeOptions(sets, { parentLabel: "#1" }))).toMatchObject({
      label: "Failed (2)",
      tip: "2 rows have a failing try on #1; 2 fail every try",
    });
  });
});
