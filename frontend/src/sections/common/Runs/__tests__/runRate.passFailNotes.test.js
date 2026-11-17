// Success counts a finished try by its Pass/fail evaluations. A try that
// finished with no Pass/fail result is not counted, and is named for that,
// apart from the tries that did not finish; when a run has both, one
// clause names them. A run with nothing to count has no Success figure and
// says what it needs.
import { describe, expect, it } from "vitest";
import {
  SUCCESS_NEEDS_PASS_FAIL,
  runRate,
} from "src/sections/common/Runs/runNumbers";

const NEEDS = "Success needs an evaluation with a Pass/fail result.";

// One row per try: [the try's value on the matrix, the try's status].
const matrixOf = (tries, column = {}) => ({
  rows: tries.map(([value, status], i) => ({
    row_id: `row-${i}`,
    cells: {
      r1: {
        trials: [value],
        trace_summary: [{ status, trial_number: 1, carried: false }],
      },
    },
  })),
  columns: {
    r1: {
      trials: 1,
      status: "completed",
      trial_count_source: "immutable_plan",
      ...column,
    },
  },
});
const rateOf = (tries, column) =>
  runRate({ matrix: matrixOf(tries, column), runId: "r1" });

describe("Success, counted by Pass/fail evaluations", () => {
  it("names the finished tries with no Pass/fail result", () => {
    const rate = rateOf(
      [
        [true, "completed"],
        [false, "completed"],
        [null, "completed"],
        [null, "completed"],
        ["error", "error"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(rate.k).toBe(1);
    expect(rate.n).toBe(3);
    expect(rate.rate).toBeCloseTo(1 / 3, 10);
    expect(rate.note).toBe("2 tries have no Pass/fail result · not counted");
  });

  it("says it of one try in the singular", () => {
    const rate = rateOf(
      [
        [true, "completed"],
        [null, "completed"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(rate.note).toBe("1 try has no Pass/fail result · not counted");
  });

  it("keeps its own words for the tries that did not finish", () => {
    const rate = rateOf(
      [
        [true, "completed"],
        [null, "queued"],
        ["infra", "infra"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(rate.note).toBe("2 tries didn't finish · not counted");
  });

  it("names both kinds in one clause when a run has both", () => {
    const rate = rateOf(
      [
        [true, "completed"],
        [null, "running"],
        [null, "completed"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(rate.note).toBe(
      "1 try didn't finish and 1 try has no Pass/fail result · not counted",
    );
  });

  it("counts each kind in the clause, singular or plural", () => {
    const some = rateOf(
      [
        [true, "completed"],
        [null, "queued"],
        ["infra", "infra"],
        [null, "completed"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(some.note).toBe(
      "2 tries didn't finish and 1 try has no Pass/fail result · not counted",
    );
    const others = rateOf(
      [
        [true, "completed"],
        [null, "running"],
        [null, "completed"],
        [null, "completed"],
      ],
      { pass_fail_evals: 1 },
    );
    expect(others.note).toBe(
      "1 try didn't finish and 2 tries have no Pass/fail result · not counted",
    );
  });

  it("gives no figure to a run with nothing to count, and says why", () => {
    const rate = rateOf(
      [
        [null, "completed"],
        [null, "completed"],
        ["error", "error"],
      ],
      { pass_fail_evals: 0 },
    );
    expect(rate.rate).toBeNull();
    expect(rate.n).toBe(0);
    expect(rate.noPassFail).toBe(true);
    expect(rate.note).toBe(NEEDS);
    expect(SUCCESS_NEEDS_PASS_FAIL).toBe(NEEDS);
  });

  it("reads tries with no status as not finished, as before", () => {
    const rate = runRate([[null], [true]]);
    expect(rate.rate).toBe(1);
    expect(rate.note).toBe("1 try didn't finish · not counted");
  });
});
