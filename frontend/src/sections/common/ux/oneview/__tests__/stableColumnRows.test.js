import { describe, expect, it } from "vitest";
import { stableColumnRows } from "../columnLayout";

const cols = [
  { id: "row", group: "system" },
  { id: "title", group: "metadata" },
  { id: "instruction", group: "input" },
  { id: "source", group: "system" },
];

describe("stableColumnRows (IT-R1 G5 P11)", () => {
  it("keeps the opened order when the layout reorders (freeze moves a column first)", () => {
    const layoutAfterFreeze = [cols[2], cols[0], cols[1], cols[3]];
    const order = cols.map((c) => c.id);
    expect(stableColumnRows(layoutAfterFreeze, order).map((c) => c.id)).toEqual(
      ["row", "source", "title", "instruction"],
    );
  });

  it("keeps each group together in the caller's order, so headings never repeat", () => {
    const out = stableColumnRows(
      cols,
      cols.map((c) => c.id),
      ["input", "metadata", "system"],
    );
    expect(out.map((c) => c.group)).toEqual([
      "input",
      "metadata",
      "system",
      "system",
    ]);
  });

  it("appends new columns and works without groups", () => {
    const flat = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(stableColumnRows(flat, ["b", "a"]).map((c) => c.id)).toEqual([
      "b",
      "a",
      "c",
    ]);
  });
});
