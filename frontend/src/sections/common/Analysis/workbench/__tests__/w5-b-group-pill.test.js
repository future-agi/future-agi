// W5 B1: a group's own results filter. Expression groups get the expression
// equal to the group's value (kept by the caller only when it selects exactly
// the group's rows); metadata and split groups keep their pills.
import { describe, expect, it } from "vitest";
import { groupRowsPill, ownGroupPill, sameIds } from "../groupPill";
import { MISSING } from "../workbenchModel";

describe("ownGroupPill", () => {
  it("an expression group: the expression equal to the group's value", () => {
    expect(
      ownGroupPill("expr:len(customer_message) > 120", "true", {}),
    ).toEqual({
      type: "expression",
      expr: "(len(customer_message) > 120) = true",
    });
    expect(ownGroupPill("expr:metadata.tier", "enterprise", {}).expr).toBe(
      '(metadata.tier) = "enterprise"',
    );
    expect(ownGroupPill("expr:round(row / 10)", "3", {}).expr).toBe(
      "(round(row / 10)) = 3",
    );
    expect(ownGroupPill("expr:metadata.tier", MISSING, {})).toBeNull();
  });
  it("metadata and split groups keep their own pills", () => {
    const summary = {
      metadata_keys: ["difficulty"],
      metadata_values: { difficulty: ["easy", "hard"] },
      splits: [{ id: "s1", name: "smoke" }],
    };
    expect(ownGroupPill("meta:difficulty", "easy", summary)).toEqual({
      type: "meta",
      key: "difficulty",
      op: "eq",
      value: "easy",
    });
    expect(ownGroupPill("meta:split", "smoke", summary)).toEqual({
      type: "split",
      values: ["s1"],
    });
    expect(ownGroupPill("col:intent", "refund", summary)).toBeNull();
  });
  it("the rows pill names the group and holds exactly its ids", () => {
    const pill = groupRowsPill("expr:metadata.tier", "pro", ["a", "b"]);
    expect(pill.type).toBe("rows");
    expect(pill.label).toBe("Expression · metadata.tier: pro");
    expect(sameIds(["a", "b"], ["b", "a"])).toBe(true);
    expect(sameIds(["a"], ["a", "b"])).toBe(false);
  });
});

describe("customError", () => {
  it("names the column's own error from the values answer", async () => {
    const { customError } = await import("../customColumnGroups");
    const res = {
      columns: [{ id: "c1", name: "long_message" }],
      errors: { c1: "Unknown field “customer_message”." },
    };
    expect(customError(res, "long_message")).toBe(
      "Unknown field “customer_message”.",
    );
    expect(customError(res, "other")).toBeNull();
    expect(
      customError({ columns: res.columns, errors: {} }, "long_message"),
    ).toBeNull();
  });
});
