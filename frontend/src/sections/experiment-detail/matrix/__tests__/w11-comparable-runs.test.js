// The results endpoint lines up only runs that share one dataset version,
// trial plan and evaluators (results_selection.py, "mixed comparison is not
// supported"). The Matrix reads only such runs: the baseline's group on
// Compare runs, and it names the runs it leaves out in one plain line. On
// the experiment page it names the experiment's runs that can't be lined
// up with the one run shown. Pure.
import { describe, expect, it } from "vitest";
import {
  comparableRunIds,
  comparisonKeyOf,
  leftOutLine,
  otherGroupIds,
} from "../comparableRuns";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";
const RECORDS = [
  { run_id: A, label: "#1", comparison_group: "plan-old" },
  { run_id: B, label: "#2", comparison_group: "plan-new" },
  { run_id: C, label: "#3", comparison_group: "plan-new" },
];
const labelOf = (id) => RECORDS.find((r) => r.run_id === id)?.label;

describe("the runs the Matrix reads", () => {
  it("keeps the baseline's group and leaves out the others, in order", () => {
    expect(
      comparableRunIds([A, B, C], { baselineId: B, runs: RECORDS }),
    ).toEqual({ runIds: [B, C], left: [A] });
    expect(comparableRunIds([A, B], { baselineId: B, runs: RECORDS })).toEqual({
      runIds: [B],
      left: [A],
    });
    // Runs that line up are all read.
    expect(comparableRunIds([B, C], { baselineId: B, runs: RECORDS })).toEqual({
      runIds: [B, C],
      left: [],
    });
  });

  it("reads every run when it can't tell (no run records yet)", () => {
    expect(comparableRunIds([A, B], { baselineId: B, runs: [] })).toEqual({
      runIds: [A, B],
      left: [],
    });
    expect(comparableRunIds([A, B], { baselineId: B })).toEqual({
      runIds: [A, B],
      left: [],
    });
  });

  it("groups runs as Run history does", () => {
    expect(comparisonKeyOf({ run_id: A })).toBe("legacy");
    expect(comparisonKeyOf({ run_id: A, comparison_group: null })).toBe(
      `run:${A}`,
    );
    expect(comparisonKeyOf({ run_id: A, comparison_group: "g" })).toBe("g");
  });

  it("finds the runs that can't be lined up with the runs shown", () => {
    expect(otherGroupIds([B], RECORDS)).toEqual([A]);
    expect(otherGroupIds([B, C], RECORDS)).toEqual([A]);
    expect(otherGroupIds([A], RECORDS)).toEqual([B, C]);
    // A run of the same group is not one of them.
    expect(otherGroupIds([B], [RECORDS[1], RECORDS[2]])).toEqual([]);
    // Nothing is known yet.
    expect(otherGroupIds([B], [])).toEqual([]);
    expect(otherGroupIds([B], undefined)).toEqual([]);
  });

  it("says in one plain line which runs it shows and which it leaves out", () => {
    expect(leftOutLine([A, B, C], [A], labelOf)).toBe(
      "Showing #2 and #3. #1 is left out: it uses a different dataset version, trial plan or evaluators.",
    );
    expect(leftOutLine([A, B], [A], labelOf)).toBe(
      "#1 and #2 can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Pick runs that share all three.",
    );
    expect(leftOutLine([B, A], [A], labelOf)).toBe(
      "#2 and #1 can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Pick runs that share all three.",
    );
    expect(leftOutLine([B, C], [], labelOf)).toBeNull();
  });
});
