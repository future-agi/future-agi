// The Matrix run header: a run with nothing to count (no evaluation with a
// Pass/fail result) shows no Success figure, says what it needs on hover,
// counts its errors without calling them fails, and none of its rows is
// counted better or worse. A try the server sends as a fail reads as a failed
// try, never as one with no pass/fail result; a finished try one of whose
// evaluations passed while another gave none is missing a Pass/fail result.
import { describe, expect, it } from "vitest";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { columnStats, unscoredResultText } from "../matrixModel";
import { trialTooltip } from "../MatrixCell";
import headerSource from "../MatrixHeader.jsx?raw";

const NONE = "run-none";
const PASSES = "run-passes";

// Two tries per row. NONE has no Pass/fail evaluation and errored on every
// try; PASSES passed every try.
const tries = (value, status) => ({
  trials: [value, value],
  trace_summary: [1, 2].map((n) => ({
    status,
    trial_number: n,
    carried: false,
  })),
  failed_evals: [[], []],
});
const column = (label, passFailEvals) => ({
  label,
  trials: 2,
  status: "completed",
  trial_count_source: "immutable_plan",
  pass_fail_evals: passFailEvals,
});
const MATRIX = {
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    cells: {
      [NONE]: tries("error", "error"),
      [PASSES]: tries(true, "completed"),
    },
  })),
  columns: { [NONE]: column("#1", 0), [PASSES]: column("#2", 1) },
};

describe("the Matrix header of a run with nothing to count", () => {
  it("shows no Success figure and says what it needs", () => {
    const s = columnStats(MATRIX, NONE, null);
    expect(s.passAt1Text).toBe("—");
    expect(s.passAt1.n).toBe(0);
    expect(s.passHatKText).toBe("—");
    expect(s.noPassFail).toBe(true);
    expect(s.passAt1Tip).toBe(SUCCESS_NEEDS_PASS_FAIL);
  });

  it("puts that tip on the header's figure", () => {
    expect(headerSource).toMatch(/title=\{stats\.passAt1Tip\}/);
  });

  it("counts none of its rows better or worse, as candidate or as baseline", () => {
    const asCandidate = columnStats(MATRIX, NONE, PASSES);
    expect([asCandidate.better, asCandidate.worse]).toEqual([0, 0]);
    const asBaseline = columnStats(MATRIX, PASSES, NONE);
    expect([asBaseline.better, asBaseline.worse]).toEqual([0, 0]);
  });

  it("counts its errors without saying they count as fails", () => {
    expect(columnStats(MATRIX, NONE, null).errorText).toBe("4 errors");
    const one = {
      rows: [
        { row_id: "row-1", n: 1, cells: { [NONE]: tries("error", "error") } },
      ],
      columns: { [NONE]: { ...column("#1", 0), trials: 1 } },
    };
    one.rows[0].cells[NONE].trials = ["error"];
    expect(columnStats(one, NONE, null).errorText).toBe("1 error");
  });

  it("leaves a run with something counted as it was", () => {
    // Green at the base.
    const s = columnStats(MATRIX, PASSES, null);
    expect(s.passAt1Text).toBe("100% (4/4)");
    expect(s.passAt1Tip ?? null).toBeNull();
  });
});

describe("the Matrix header of a run whose errors are in its figure", () => {
  it("says they count as fails", () => {
    // Green at the base.
    const mixed = {
      rows: [1, 2].map((n) => ({
        row_id: `row-${n}`,
        n,
        cells: {
          [PASSES]: {
            trials: ["error", true],
            trace_summary: [
              { status: "error", trial_number: 1, carried: false },
              { status: "completed", trial_number: 2, carried: false },
            ],
            failed_evals: [[], []],
          },
        },
      })),
      columns: { [PASSES]: column("#2", 1) },
    };
    const s = columnStats(mixed, PASSES, null);
    expect(s.passAt1Text).toBe("50.0% (2/4)");
    expect(s.errorText).toBe("2 errors · counted as fails");
  });
});

describe("a try the server sends as a fail", () => {
  // Green at the base: the Matrix reads the matrix try.
  const cell = {
    trials: [false],
    trace_summary: [{ status: "completed", trial_number: 1, carried: false }],
    failed_evals: [["output-check"]],
  };

  it("reads as a failed try naming the failed evaluation", () => {
    expect(trialTooltip(cell, 0)).toBe("Trial 1 · failed · output-check ✕");
  });

  it("is a counted try, so the cell says nothing about a missing result", () => {
    expect(unscoredResultText(cell)).toBeNull();
  });
});

describe("a finished try that is not counted, in the Matrix", () => {
  const notCounted = (passed) => ({
    trials: [null],
    trace_summary: [{ status: "completed", trial_number: 1, carried: false }],
    failed_evals: [[]],
    passed_evals: [passed],
  });

  it("is missing a Pass/fail result when one evaluation passed and another gave none", () => {
    expect(trialTooltip(notCounted(["output-check"]), 0)).toBe(
      "Trial 1 · done · is missing a Pass/fail result",
    );
  });

  it("has no pass/fail result when none of its evaluations gave one", () => {
    // Green at the base.
    expect(trialTooltip(notCounted([]), 0)).toBe(
      "Trial 1 · done · no pass/fail result",
    );
  });
});
