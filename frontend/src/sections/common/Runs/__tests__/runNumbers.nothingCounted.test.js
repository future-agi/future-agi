// A run with nothing to count (no evaluation with a Pass/fail result) has no
// Success figure anywhere: "Best" passes over it and a comparison never calls
// its rows better or worse. A try the server sends as a fail is counted as
// one and named in no note. A finished try that is not counted says whether
// it is missing a Pass/fail result (one evaluation passed, another gave
// none) or has none at all.
import { describe, expect, it } from "vitest";
import {
  bestOf,
  compareRuns,
  runRate,
  runsOfMatrix,
  SUCCESS_NEEDS_PASS_FAIL,
} from "../runNumbers";
import { leaderboardNumbers } from "src/sections/develop-detail/ExperimentTab/leaderboardVersions";

const NONE = "run-none";
const FAILS = "run-fails";
const PASSES = "run-passes";
const NOTHING = "run-nothing";

// One try per row: [value, status]; `failed` names the failed evaluation.
const cell = ([value, status], failed = []) => ({
  trials: [value],
  trace_summary: [{ status, trial_number: 1, carried: false }],
  failed_evals: [failed],
});
const column = (label, passFailEvals) => ({
  label,
  trials: 1,
  status: "completed",
  trial_count_source: "immutable_plan",
  pass_fail_evals: passFailEvals,
});

// NONE has no Pass/fail evaluation and errored on both rows; NOTHING has
// none either and no try errored; FAILS failed both rows; PASSES passed both.
const MATRIX = {
  rows: [1, 2].map((n) => ({
    row_id: `row-${n}`,
    n,
    cells: {
      [NONE]: cell(["error", "error"]),
      [NOTHING]: cell([null, "completed"]),
      [FAILS]: cell([false, "completed"], ["check"]),
      [PASSES]: cell([true, "completed"]),
    },
  })),
  columns: {
    [NONE]: column("#1", 0),
    [NOTHING]: column("#2", 0),
    [FAILS]: column("#3", 1),
    [PASSES]: column("#4", 1),
  },
};
const only = (ids) => runsOfMatrix(MATRIX, { runIds: ids });

describe("Best passes over a run with nothing to count", () => {
  it("names no Best when no run has anything counted", () => {
    expect(bestOf(only([NONE, NOTHING]))).toBeNull();
  });

  it("names the counted run Best even when it failed every try", () => {
    expect(bestOf(only([NONE, FAILS]))?.id).toBe(FAILS);
  });

  it("names no Best on the Experiments list when no run has anything counted", () => {
    const runs = [NONE, NOTHING].map((id, i) => ({
      run_id: id,
      label: `#${i + 1}`,
      status: "Completed",
      listed: true,
    }));
    expect(leaderboardNumbers(runs, MATRIX, { noun: "task" }).best).toBeNull();
  });

  it("gives a run with nothing to count no rate, in the form runsOfMatrix passes", () => {
    const [none] = only([NONE]);
    const rate = runRate(none);
    expect(rate.rate).toBeNull();
    expect(rate.noPassFail).toBe(true);
    expect(rate.note).toBe(SUCCESS_NEEDS_PASS_FAIL);
  });
});

describe("a comparison never calls a run with nothing to count better or worse", () => {
  it("as the candidate", () => {
    const c = compareRuns({
      matrix: MATRIX,
      baselineId: PASSES,
      candidateId: NONE,
    });
    expect(c.worse).toBe(0);
    expect(c.better).toBe(0);
    expect(c.worseIds).toEqual([]);
    expect(c.candidate.rate).toBeNull();
    expect(c.deltaPts).toBeNull();
    expect(c.rows.map((r) => r.change)).toEqual(["not_run", "not_run"]);
  });

  it("as the baseline", () => {
    const c = compareRuns({
      matrix: MATRIX,
      baselineId: NONE,
      candidateId: FAILS,
    });
    expect(c.better).toBe(0);
    expect(c.worse).toBe(0);
    expect(c.baseline.rate).toBeNull();
    expect(c.deltaPts).toBeNull();
  });

  it("still compares two runs that both have something counted", () => {
    // Green at the base: the comparison of counted runs is unchanged.
    const c = compareRuns({
      matrix: MATRIX,
      baselineId: PASSES,
      candidateId: FAILS,
    });
    expect(c.worse).toBe(2);
    expect(c.deltaPts).toBe(-100);
  });
});

describe("a try the server sends as a fail", () => {
  it("is counted as a fail and named in no note", () => {
    // Green at the base: the list reads the matrix try, so the server's
    // fail is all it needs.
    const rate = runRate({ matrix: MATRIX, runId: FAILS });
    expect(rate).toMatchObject({
      k: 0,
      n: 2,
      noResult: 0,
      unfinished: 0,
      note: null,
    });
  });
});

describe("a finished try that is not counted says why", () => {
  // Tries of one run, one per row: [value, status, evaluations that passed].
  const noteOf = (tries) =>
    runRate({
      matrix: {
        rows: tries.map(([value, status, passed], i) => ({
          row_id: `row-${i}`,
          cells: {
            r: {
              trials: [value],
              trace_summary: [{ status, trial_number: 1, carried: false }],
              failed_evals: [[]],
              passed_evals: [passed],
            },
          },
        })),
        columns: { r: { trials: 1, pass_fail_evals: 2 } },
      },
      runId: "r",
    });
  const PASSED = ["judge"];
  const MISSING = [null, "completed", PASSED];
  const NO_RESULT = [null, "completed", []];
  const RUNNING = [null, "running", []];
  const COUNTED = [true, "completed", ["judge", "check"]];

  it("is missing a Pass/fail result when one evaluation passed and another gave none", () => {
    const rate = noteOf([COUNTED, MISSING]);
    expect(rate.note).toBe("1 try is missing a Pass/fail result · not counted");
    expect(rate).toMatchObject({
      k: 1,
      n: 1,
      missing: 1,
      noResult: 0,
      excluded: 1,
    });
  });

  it("says so for more than one try", () => {
    expect(noteOf([COUNTED, MISSING, MISSING]).note).toBe(
      "2 tries are missing a Pass/fail result · not counted",
    );
  });

  it("keeps 'has no Pass/fail result' for a try with no result at all", () => {
    // Green at the base.
    const rate = noteOf([COUNTED, NO_RESULT]);
    expect(rate.note).toBe("1 try has no Pass/fail result · not counted");
    expect(rate.noResult).toBe(1);
  });

  it("names both kinds in one clause", () => {
    expect(noteOf([COUNTED, NO_RESULT, MISSING]).note).toBe(
      "1 try has no Pass/fail result and 1 try is missing a Pass/fail result · not counted",
    );
  });

  it("names all three kinds in one clause", () => {
    expect(noteOf([COUNTED, RUNNING, NO_RESULT, MISSING]).note).toBe(
      "1 try didn't finish, 1 try has no Pass/fail result and 1 try is missing a Pass/fail result · not counted",
    );
  });
});
