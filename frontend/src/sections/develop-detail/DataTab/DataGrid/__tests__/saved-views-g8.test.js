// v2.1 g8 (grid:saved-views, proof walk #7): a saved view keeps the column
// filters, the expression and the pills, and Reset (the default) is not
// "modified".
import { describe, expect, it } from "vitest";
import { toolbarViewState, toolbarViewModified } from "../useDataToolbarView";
import { DEFAULT_DENSITY } from "../dataGridView";

const filter = (value) => ({
  column_id: "c1",
  filterConfig: {
    filterType: "text",
    filterOp: "contains",
    filterValue: value,
  },
});

describe("saved view state", () => {
  it("keeps active filters, the expression and the pills", () => {
    const v = toolbarViewState({
      search: "refund",
      gridView: {
        failedOnly: true,
        density: "wrap",
        expression: "len(answer) > 40",
        statuses: ["approved"],
        tags: ["edge"],
      },
      splits: "s1",
      filters: [filter("A-77"), filter("")],
    });
    expect(v).toEqual({
      q: "refund",
      failed: true,
      fevals: [],
      density: "wrap",
      splits: "s1",
      expr: "len(answer) > 40",
      filters: [filter("A-77")],
      statuses: ["approved"],
      tags: ["edge"],
      sort: [],
    });
    expect(toolbarViewModified(v)).toBe(true);
  });

  it("the default view is not modified; any one part makes it so", () => {
    const base = toolbarViewState({ gridView: { density: DEFAULT_DENSITY } });
    expect(toolbarViewModified(base)).toBe(false);
    expect(toolbarViewModified({ ...base, expr: "x > 1" })).toBe(true);
    expect(toolbarViewModified({ ...base, filters: [filter("a")] })).toBe(true);
    expect(toolbarViewModified({ ...base, tags: ["t"] })).toBe(true);
  });
});
