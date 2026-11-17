import { describe, expect, it } from "vitest";
import {
  emptyReferenceSentence,
  emptyReferenceWarnings,
  foldedEvalNames,
  selectedRowValues,
} from "../evalCoverage";

const columns = [
  { id: "c1", name: "customer_message", role: "input" },
  { id: "c3", name: "expected_reply", role: "expected" },
];
const correctness = {
  name: "Correctness",
  config: { mapping: { output: "output", expected: "c3", input: "c1" } },
};
const tone = { name: "Tone", config: { mapping: { output: "output" } } };

describe("eval coverage on the selected rows (IT-R1 G5 P6)", () => {
  it("reads unsaved door rows from the change-set", () => {
    const ops = [
      { op: "add_row", temp_id: "imp-1", cells: { c1: "hi" } },
      { op: "add_row", temp_id: "imp-2", cells: { c1: "yo", c3: "" } },
    ];
    const selected = selectedRowValues({ rowIds: ["imp-1", "imp-2"], ops });
    const warnings = emptyReferenceWarnings({
      evals: [correctness, tone],
      columns,
      selected,
    });
    expect(warnings).toHaveLength(1);
    expect(emptyReferenceSentence(warnings[0])).toBe(
      "Correctness needs expected_reply — empty on 2 of 2 selected rows; it will skip them.",
    );
  });

  it("reads saved rows from the sample table and never guesses", () => {
    const rows = [
      { row_id: "R1", c3: { cell_value: "Sorry" } },
      { row_id: "R2", c3: { cell_value: null } },
    ];
    const sel = selectedRowValues({ rowIds: ["R1", "R2"], rows });
    expect(
      emptyReferenceWarnings({
        evals: [correctness],
        columns,
        selected: sel,
      })[0],
    ).toMatchObject({ empty: 1, total: 2 });
    expect(selectedRowValues({ rowIds: ["R9"], rows })).toBeNull();
    expect(selectedRowValues({ rows, exampleCount: 248 })).toBeNull();
  });

  it("folds an eval that is one of another eval's checks", () => {
    expect(
      foldedEvalNames([
        {
          name: "Task success",
          checkNames: ["Callback booked", "Task reply rubric v1"],
        },
        { name: "Task reply rubric v1", checkNames: [] },
      ]),
    ).toEqual({ "Task reply rubric v1": "Task success" });
  });
});
