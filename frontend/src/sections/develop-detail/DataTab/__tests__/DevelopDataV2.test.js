import { describe, it, expect } from "vitest";
import { getAverageColumnConfig } from "../DevelopDataV2";

// getAverageColumnConfig feeds AG Grid's `pinnedBottomRowData`. It must only
// produce a pinned summary row when a column actually has an average to show;
// otherwise the grid renders a blank placeholder row at the bottom.
describe("getAverageColumnConfig", () => {
  it("returns no pinned row when no column has a value to summarise", () => {
    const columns = [
      { id: "col1", metadata: {} },
      { id: "col2", metadata: {} },
    ];

    expect(getAverageColumnConfig(columns, [])).toEqual([]);
  });

  it("returns a pinned row when a column has an average score", () => {
    const columns = [{ id: "col1", averageScore: 87, metadata: {} }];

    const result = getAverageColumnConfig(columns, []);

    expect(result).toHaveLength(1);
    expect(result[0].col1).toBe("Average : 87%");
  });

  it("keeps the pinned row for a genuine 0% average (0 is a value, not empty)", () => {
    const columns = [{ id: "col1", averageScore: 0, metadata: {} }];

    const result = getAverageColumnConfig(columns, []);

    expect(result).toHaveLength(1);
    expect(result[0].col1).toBe("Average : 0%");
  });

  it("returns no pinned row when there are no columns", () => {
    expect(getAverageColumnConfig([], [])).toEqual([]);
    expect(getAverageColumnConfig(undefined, undefined)).toEqual([]);
  });
});
