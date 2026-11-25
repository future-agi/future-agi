import { describe, expect, it } from "vitest";

import { filterKeyOf, operatorOfKey } from "../filterKeys";

const FIELD_MAP = {
  name: { type: "string" },
  use_case: { type: "enum" },
};

describe("filterKeyOf", () => {
  it.each([
    ["contains", "name__contains"],
    ["not_contains", "name__not_contains"],
    ["starts_with", "name__starts_with"],
    ["equals", "name"],
    ["not_equals", "name_not"],
  ])("puts a text row's %s on the key", (operator, key) => {
    expect(filterKeyOf({ field: "name", operator }, "string", true)).toBe(key);
  });

  it("leaves an enum row's key bare", () => {
    expect(
      filterKeyOf({ field: "use_case", operator: "is" }, "enum", true),
    ).toBe("use_case");
    expect(
      filterKeyOf({ field: "use_case", operator: "is_not" }, "enum", true),
    ).toBe("use_case_not");
  });

  it("drops the operator for callers that did not opt in", () => {
    expect(filterKeyOf({ field: "name", operator: "contains" }, "string")).toBe(
      "name",
    );
  });
});

describe("operatorOfKey", () => {
  it.each([
    ["name__contains", "contains"],
    ["name__starts_with", "starts_with"],
    ["name", "equals"],
    ["name_not", "not_equals"],
  ])("reads %s back as %s", (key, operator) => {
    expect(operatorOfKey(key, FIELD_MAP, true)).toEqual({
      field: "name",
      operator,
    });
  });
});
