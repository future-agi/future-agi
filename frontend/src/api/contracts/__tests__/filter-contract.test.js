import { describe, expect, it } from "vitest";

import {
  buildApiFilterFromPanelRow,
  coerceFilterValue,
  isAllowedFilterOperator,
  normalizeFilterOperator,
  normalizeFilterType,
} from "../filter-contract";
import {
  FILTER_CONTRACT_VERSION,
  SPAN_ATTRIBUTE_ALLOWED_OPS,
} from "../filter-contract.generated";

describe("filter contract", () => {
  it("loads the generated contract artifact", () => {
    expect(FILTER_CONTRACT_VERSION).toBe(1);
    expect(SPAN_ATTRIBUTE_ALLOWED_OPS.number).toContain("not_between");
    expect(SPAN_ATTRIBUTE_ALLOWED_OPS.number).not.toContain("not_in_between");
  });

  it("normalizes UI and legacy operators to canonical API operators", () => {
    expect(normalizeFilterOperator("is")).toBe("equals");
    expect(normalizeFilterOperator("is_not")).toBe("not_equals");
    expect(normalizeFilterOperator("not_in_between")).toBe("not_between");
    expect(normalizeFilterOperator("before")).toBe("less_than");
  });

  it("promotes multi-value equality to in/not_in", () => {
    expect(
      normalizeFilterOperator("is", {
        filterType: "categorical",
        value: ["OK", "ERROR"],
      }),
    ).toBe("in");
    expect(
      normalizeFilterOperator("is_not", {
        filterType: "annotator",
        value: ["user-a", "user-b"],
      }),
    ).toBe("not_in");
  });

  it("coerces values to the backend wire shape", () => {
    expect(coerceFilterValue(["10", "20"], "between", "number")).toEqual([
      10, 20,
    ]);
    expect(coerceFilterValue("true", "equals", "boolean")).toBe(true);
    expect(coerceFilterValue("x", "in", "text")).toEqual(["x"]);
  });

  it("builds canonical API filters from observe panel rows", () => {
    expect(
      buildApiFilterFromPanelRow({
        field: "latency_ms",
        fieldName: "Latency",
        fieldCategory: "system",
        fieldType: "number",
        operator: "greater_than",
        value: ["100"],
      }),
    ).toEqual({
      column_id: "latency_ms",
      display_name: "Latency",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 100,
        col_type: "SYSTEM_METRIC",
      },
    });
  });

  it("keeps the API contract explicit per type", () => {
    expect(normalizeFilterType("string")).toBe("text");
    expect(isAllowedFilterOperator("number", "contains")).toBe(false);
    expect(isAllowedFilterOperator("number", "not_between")).toBe(true);
  });
});
