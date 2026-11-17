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
    const columns = [{ id: "col1", average_score: 87, metadata: {} }];

    const result = getAverageColumnConfig(columns, []);

    expect(result).toHaveLength(1);
    expect(result[0].col1).toBe("Average : 87%");
    // AG Grid's getRowId runs on pinned rows as well.
    expect(result[0].row_id).toBe("__average__");
  });

  it("keeps the pinned row for a genuine 0% average (0 is a value, not empty)", () => {
    const columns = [{ id: "col1", average_score: 0, metadata: {} }];

    const result = getAverageColumnConfig(columns, []);

    expect(result).toHaveLength(1);
    expect(result[0].col1).toBe("Average : 0%");
  });

  it("returns no pinned row when there are no columns", () => {
    expect(getAverageColumnConfig([], [])).toEqual([]);
    expect(getAverageColumnConfig(undefined, undefined)).toEqual([]);
  });

  // The API (GetDatasetTableView, column_config_only=true) sends snake_case:
  // metadata.average_latency / average_tokens / average_cost on run_prompt
  // columns.
  it("returns a pinned row for run-prompt latency, tokens and cost", () => {
    const columns = [
      {
        id: "col1",
        metadata: {
          average_latency: 812.4,
          average_tokens: 356.2,
          average_cost: 0.000214,
        },
      },
    ];

    const result = getAverageColumnConfig(columns, []);

    expect(result).toHaveLength(1);
    expect(result[0].col1).toBeTruthy();
  });

  it("shows a numeric-output eval average without a percent sign", () => {
    const columns = [{ id: "col1", average_score: 4.2, metadata: {} }];
    const rows = [{ col1: { value_infos: { output: "numeric" } } }];

    expect(getAverageColumnConfig(columns, rows)[0].col1).toBe("Average : 4.2");
  });
});
