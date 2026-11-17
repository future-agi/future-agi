// @vitest-environment jsdom
// L1 fix round 1 (the capability contract): every dataset created on dev has
// columns with no role. The Row tab folded `No role` and said "This dataset
// has no columns yet." right under Column 1 and Column 2. The empty line is for a row with
// no field at all; `No role` opens when it is the only section with fields.
import { describe, expect, it } from "vitest";
import { rowFieldsLayout } from "../../DatapointDrawerV2/RowFieldsTab";

const f = (name) => ({ column: { id: name, name }, value: "v" });
const EMPTY = {
  input: [],
  expected: [],
  outputs: [],
  evals: [],
  human: [],
  metadata: [],
  noRole: [],
};

describe("L1 the Row tab's sections on a dataset made on dev", () => {
  it("a row with only no-role columns is not empty and opens No role", () => {
    const layout = rowFieldsLayout({
      ...EMPTY,
      noRole: [f("Column 1"), f("Column 2")],
    });
    expect(layout).toEqual({ empty: false, noRoleOpen: true });
  });

  it("says the dataset has no columns only when no section has a field", () => {
    expect(rowFieldsLayout(EMPTY)).toEqual({
      empty: true,
      noRoleOpen: false,
    });
    expect(rowFieldsLayout({})).toEqual({ empty: true, noRoleOpen: false });
  });

  it("keeps No role folded beside fields that have a role", () => {
    for (const k of [
      "input",
      "expected",
      "outputs",
      "evals",
      "human",
      "metadata",
    ]) {
      const layout = rowFieldsLayout({
        ...EMPTY,
        [k]: [f("question")],
        noRole: [f("Column 1")],
      });
      expect(layout, k).toEqual({ empty: false, noRoleOpen: false });
    }
  });

  it("a row with only metadata fields is not empty either", () => {
    expect(rowFieldsLayout({ ...EMPTY, metadata: [f("source")] }).empty).toBe(
      false,
    );
  });
});
