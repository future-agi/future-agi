import { describe, expect, it } from "vitest";
import { NOT_PERSONAL, applyFileChoice, choiceOf } from "../personalData";

const columns = [
  { name: "order_id", sensitive: false, mode: NOT_PERSONAL },
  { name: "name", sensitive: true, mode: NOT_PERSONAL },
  { name: "email", sensitive: true, mode: "retain" },
];
const modes = (cols) => cols.map((column) => column.mode);

describe("personal data choice", () => {
  it("fills every found column with Replace consistently", () => {
    expect(modes(applyFileChoice(columns, "replace"))).toEqual([
      NOT_PERSONAL,
      "replace",
      "replace",
    ]);
  });

  it("turns custom once one column differs from the file's choice", () => {
    const replaced = applyFileChoice(columns, "replace");
    const changed = replaced.map((column) =>
      column.name === "email" ? { ...column, mode: "mask" } : column,
    );
    expect(choiceOf(replaced, "replace")).toBe("replace");
    expect(choiceOf(changed, "replace")).toBe("custom");
  });

  it("resets every column when Keep is picked after custom", () => {
    const custom = [
      { name: "order_id", sensitive: false, mode: "mask" },
      { name: "name", sensitive: true, mode: "omit" },
      { name: "email", sensitive: true, mode: "replace" },
    ];
    expect(modes(applyFileChoice(custom, "keep"))).toEqual([
      "retain",
      "retain",
      "retain",
    ]);
  });
});
