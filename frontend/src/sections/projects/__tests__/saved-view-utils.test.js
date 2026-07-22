import { describe, expect, it } from "vitest";

import { filtersContentEqual } from "../saved-view-utils";

const filter = (overrides = {}) => ({
  column_id: "latency_ms",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "number",
    filter_op: "greater_than",
    filter_value: 100,
  },
  ...overrides,
});

describe("saved-view-utils", () => {
  it("compares canonical saved-view filters deeply", () => {
    expect(filtersContentEqual([filter()], [filter()])).toBe(true);
    expect(
      filtersContentEqual(
        [filter()],
        [
          filter({
            filter_config: { ...filter().filter_config, filter_value: 200 },
          }),
        ],
      ),
    ).toBe(false);
  });

  it("does not treat legacy camelCase filter payloads as equivalent", () => {
    const legacy = {
      columnId: "latency_ms",
      filterConfig: {
        colType: "SYSTEM_METRIC",
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 100,
      },
    };

    expect(filtersContentEqual([filter()], [legacy])).toBe(false);
  });
});
