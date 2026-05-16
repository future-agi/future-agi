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
  FILTER_TYPE_ALLOWED_OPS,
  SPAN_ATTRIBUTE_ALLOWED_OPS,
} from "../filter-contract.generated";

const valueFor = (filterType, operator) => {
  if (operator === "is_null" || operator === "is_not_null") return "ignored";
  if (operator === "between" || operator === "not_between") {
    return filterType === "number"
      ? ["10", "20"]
      : ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"];
  }
  if (operator === "in" || operator === "not_in") {
    if (filterType === "thumbs") return ["Thumbs Up", "Thumbs Down"];
    if (filterType === "annotator") return ["user-a", "user-b"];
    return ["alpha", "beta"];
  }
  if (filterType === "number") return ["42"];
  if (filterType === "boolean") return "true";
  if (filterType === "thumbs") return "Thumbs Up";
  if (filterType === "annotator") return "user-a";
  return "alpha";
};

describe("filter contract", () => {
  it("loads the generated contract artifact", () => {
    expect(FILTER_CONTRACT_VERSION).toBe(1);
    expect(SPAN_ATTRIBUTE_ALLOWED_OPS.number).toContain("not_between");
    expect(SPAN_ATTRIBUTE_ALLOWED_OPS.number).not.toContain("not_in_between");
  });

  it("keeps operators canonical instead of translating legacy aliases", () => {
    expect(normalizeFilterOperator("equals")).toBe("equals");
    expect(normalizeFilterOperator("not_between")).toBe("not_between");
    expect(isAllowedFilterOperator("text", "is")).toBe(false);
    expect(isAllowedFilterOperator("number", "not_in_between")).toBe(false);
  });

  it("promotes multi-value equality to in/not_in", () => {
    expect(
      normalizeFilterOperator("equals", {
        filterType: "categorical",
        value: ["OK", "ERROR"],
      }),
    ).toBe("in");
    expect(
      normalizeFilterOperator("not_equals", {
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
    const apiFilter = buildApiFilterFromPanelRow({
      field: "latency_ms",
      fieldName: "Latency",
      fieldCategory: "system",
      fieldType: "number",
      operator: "greater_than",
      value: ["100"],
    });

    expect(apiFilter).toEqual({
      column_id: "latency_ms",
      display_name: "Latency",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 100,
        col_type: "SYSTEM_METRIC",
      },
    });
    expect(apiFilter).not.toHaveProperty("columnId");
    expect(apiFilter).not.toHaveProperty("filterConfig");
    expect(apiFilter.filter_config).not.toHaveProperty("filterOp");
  });

  it("keeps the API contract explicit per type", () => {
    expect(normalizeFilterType("string")).toBe("text");
    expect(isAllowedFilterOperator("number", "contains")).toBe(false);
    expect(isAllowedFilterOperator("number", "not_between")).toBe(true);
  });

  it("fails before sending a non-canonical operator to the API", () => {
    expect(() =>
      buildApiFilterFromPanelRow({
        field: "status",
        fieldType: "text",
        operator: "is",
        value: "OK",
      }),
    ).toThrow(/Unsupported filter operator/);
  });

  it.each(
    Object.entries(FILTER_TYPE_ALLOWED_OPS).flatMap(([filterType, operators]) =>
      operators.map((operator) => [filterType, operator]),
    ),
  )(
    "coerces %s/%s into the backend wire value shape",
    (filterType, operator) => {
      const value = valueFor(filterType, operator);
      const output = coerceFilterValue(value, operator, filterType);

      if (operator === "is_null" || operator === "is_not_null") {
        expect(output).toBeNull();
      } else if (operator === "between" || operator === "not_between") {
        expect(output).toHaveLength(2);
        if (filterType === "number") expect(output).toEqual([10, 20]);
      } else if (operator === "in" || operator === "not_in") {
        expect(Array.isArray(output)).toBe(true);
        expect(output.length).toBeGreaterThan(0);
      } else if (filterType === "number") {
        expect(output).toBe(42);
      } else if (filterType === "boolean") {
        expect(output).toBe(true);
      } else {
        expect(output).toBeTruthy();
      }
    },
  );

  it.each(["text", "categorical", "thumbs", "annotator"])(
    "promotes multi-select equality operators for %s filters",
    (filterType) => {
      const value = valueFor(filterType, "in");

      expect(normalizeFilterOperator("equals", { filterType, value })).toBe(
        "in",
      );
      expect(normalizeFilterOperator("not_equals", { filterType, value })).toBe(
        "not_in",
      );
    },
  );

  it.each([
    [
      "latency_ms",
      "system",
      "number",
      "greater_than",
      ["100"],
      "SYSTEM_METRIC",
    ],
    ["span.foo", "attribute", "text", "contains", "bar", "SPAN_ATTRIBUTE"],
    ["eval-score", "eval", "number", "between", ["10", "90"], "EVAL_METRIC"],
    [
      "annotation-label",
      "annotation",
      "categorical",
      "equals",
      ["yes", "no"],
      "ANNOTATION",
    ],
  ])(
    "builds canonical API filter rows for %s/%s",
    (field, fieldCategory, fieldType, operator, value, colType) => {
      const apiFilter = buildApiFilterFromPanelRow({
        field,
        fieldName: field,
        fieldCategory,
        fieldType,
        operator,
        value,
      });

      expect(apiFilter).toMatchObject({
        column_id: field,
        filter_config: {
          filter_type: fieldType,
          col_type: colType,
        },
      });
      expect(
        isAllowedFilterOperator(fieldType, apiFilter.filter_config.filter_op),
      ).toBe(true);
    },
  );
});
