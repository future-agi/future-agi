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
      column_id: "latency_ms",
      _meta: { parentProperty: "System Metrics" },
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "number",
        filter_op: "not_between",
        filter_value: ["10", "20"],
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.filter_config.filter_op).toBe("not_between");
    expect(parsed.data.filter_config.filter_value).toEqual([10, 20]);
  });
});
