// HO results R10: the compare drawer's Input leads with the grid's preview field.
import { describe, expect, it } from "vitest";
import { inputsPreviewChoice } from "src/sections/common/Preview/distinguishingField";
import { gridRowsFor, previewFieldOf } from "../usePreviewField";

const rows = [1, 2, 3].map((n) => ({
  example_id: `e${n}`,
  inputs: {
    system: "You are a support agent.",
    question: `Where is order ${n}?`,
  },
}));
const wire = (list) => ({ data: { result: { rows: list } } });

describe("compare drawer preview field", () => {
  it("finds the grid read whose rows are the drawer's", () => {
    const other = [rows[0]];
    expect(
      gridRowsFor(
        [
          [["k1"], wire(other)],
          [["k2"], wire(rows)],
        ],
        ["e1", "e2", "e3"],
        "e2",
      ),
    ).toBe(rows);
    // No exact read: the largest one holding the row.
    expect(
      gridRowsFor(
        [
          [["k1"], wire(other)],
          [["k2"], wire(rows)],
        ],
        [],
        "e1",
      ),
    ).toBe(rows);
    expect(gridRowsFor([], ["e1"], "e1")).toBeNull();
  });
  it("is the grid's chooseField answer, when the row has a value there", () => {
    expect(inputsPreviewChoice(rows).field).toBe("question");
    expect(previewFieldOf(rows, rows[1].inputs)).toBe("question");
    expect(previewFieldOf(rows, { system: "x", question: "" })).toBeNull();
    expect(previewFieldOf(null, rows[0].inputs)).toBeNull();
  });
});
