import { describe, expect, it } from "vitest";

import { getComplexFilterValidation } from "../common";
import { AdvanceNumberFilterOperators } from "src/utils/constants";

describe("ComplexFilter contract wiring", () => {
  it("uses canonical not_between for numeric range filters", () => {
    expect(AdvanceNumberFilterOperators).toContainEqual({
      label: "Not Between",
      value: "not_between",
    });
    expect(AdvanceNumberFilterOperators.map((op) => op.value)).not.toContain(
      "not_in_between",
    );

    const schema = getComplexFilterValidation();
    const parsed = schema.safeParse({
      columnId: "latency_ms",
      _meta: { parentProperty: "System Metrics" },
      filterConfig: {
        col_type: "SYSTEM_METRIC",
        filterType: "number",
        filterOp: "not_between",
        filterValue: ["10", "20"],
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.filterConfig.filterOp).toBe("not_between");
    expect(parsed.data.filterConfig.filterValue).toEqual([10, 20]);
  });
});
