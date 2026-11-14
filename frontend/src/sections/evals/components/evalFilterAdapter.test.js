import { describe, expect, it } from "vitest";
import { filtersToRows, rowsToFilters } from "./evalFilterAdapter";

describe("evalFilterAdapter", () => {
  const filters = {
    search: "toxic",
    owner: "system",
    template_type: "single",
    eval_type: ["llm", "code"],
    output_type: ["pass_fail"],
    tags: ["a", "b"],
  };

  it("round-trips the API-shaped filters through panel rows", () => {
    expect(rowsToFilters(filtersToRows(filters))).toEqual(filters);
  });

  it("returns null when there is nothing to apply", () => {
    expect(rowsToFilters([])).toBeNull();
    expect(
      rowsToFilters([{ field: "name", operator: "contains", value: [] }]),
    ).toBeNull();
    expect(filtersToRows(null)).toEqual([]);
  });

  it("splits comma-separated tag text into individual tags", () => {
    expect(
      rowsToFilters([{ field: "tags", operator: "in", value: ["x, y", "z"] }]),
    ).toEqual({ tags: ["x", "y", "z"] });
  });

  it("ignores operators the list API cannot express", () => {
    expect(
      rowsToFilters([
        { field: "eval_type", operator: "not_in", value: ["llm"] },
      ]),
    ).toBeNull();
  });
});
