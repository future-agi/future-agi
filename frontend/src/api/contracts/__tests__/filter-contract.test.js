import { describe, expect, it } from "vitest";

import {
  buildApiFilterFromPanelRow,
  coerceFilterValue,
  FILTER_STRING_MAX_UTF8_BYTES,
  hydrateStoredFilterList,
  isAllowedFilterOperator,
  normalizeFilterOperator,
  normalizeFilterType,
  serializeFilterForApi,
  truncateUtf8String,
  serializeFilterListForApi,
  TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES,
} from "../filter-contract";

describe("truncateUtf8String", () => {
  it("preserves text at the byte boundary", () => {
    expect(truncateUtf8String("abcd", 4)).toBe("abcd");
  });

  it("truncates without splitting a multibyte character", () => {
    expect(truncateUtf8String("abéz", 4)).toBe("abé");
    expect(truncateUtf8String("🙂🙂", 5)).toBe("🙂");
  });
});
import {
  FILTER_CONTRACT_VERSION,
  FILTER_TYPE_ALLOWED_OPS,
  SPAN_ATTRIBUTE_ALLOWED_OPS,
  STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS,
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
  if (
    filterType === "array" &&
    (operator === "contains" || operator === "not_contains")
  ) {
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
    expect(coerceFilterValue(["x", "y"], "contains", "array")).toEqual([
      "x",
      "y",
    ]);
  });

  it("builds canonical API filters from observe panel rows", () => {
    const apiFilter = buildApiFilterFromPanelRow({
      field: "latency_ms",
      registryId: "system_attribute:traces:latency_ms",
      fieldName: "Latency",
      fieldCategory: "system",
      fieldType: "number",
      operator: "greater_than",
      value: ["100"],
    });

    expect(apiFilter).toEqual({
      column_id: "latency_ms",
      property_id: "system_attribute:traces:latency_ms",
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

  it("preserves typed custom-attribute option provenance", () => {
    const apiFilter = buildApiFilterFromPanelRow({
      field: "attempt",
      fieldCategory: "attribute",
      fieldType: "string",
      operator: "in",
      value: ["1", 1, true],
      valueTypes: ["string", "number", "boolean"],
    });

    expect(apiFilter.filter_config).toEqual({
      filter_type: "text",
      filter_op: "in",
      filter_value: ["1", 1, true],
      col_type: "SPAN_ATTRIBUTE",
      attribute_value_types: ["string", "number", "boolean"],
    });
    expect(serializeFilterForApi(apiFilter)).toEqual(apiFilter);
  });

  it.each([
    ["normal", "ordinary exact value"],
    [
      "above the generic scalar limit",
      "x".repeat(FILTER_STRING_MAX_UTF8_BYTES + 1),
    ],
    [
      "at the typed string limit",
      "é".repeat(TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES / 2),
    ],
  ])("keeps %s typed exact attribute values filterable", (_case, value) => {
    const apiFilter = buildApiFilterFromPanelRow({
      field: "long.attribute",
      fieldCategory: "attribute",
      fieldType: "string",
      operator: "in",
      value: [value],
      valueTypes: ["string"],
    });

    expect(apiFilter.filter_config).toMatchObject({
      filter_value: [value],
      attribute_value_types: ["string"],
    });
    expect(serializeFilterForApi(apiFilter)).toEqual(apiFilter);
  });

  it("rejects a typed exact attribute value above the 16 KiB bound", () => {
    const value = `${"é".repeat(
      TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES / 2,
    )}x`;

    expect(() =>
      buildApiFilterFromPanelRow({
        field: "long.attribute",
        fieldCategory: "attribute",
        fieldType: "string",
        operator: "in",
        value: [value],
        valueTypes: ["string"],
      }),
    ).toThrow(`${TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES} UTF-8 bytes`);
  });

  it("rejects misaligned typed custom-attribute provenance", () => {
    expect(() =>
      serializeFilterForApi({
        column_id: "attempt",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["1", 1],
          col_type: "SPAN_ATTRIBUTE",
          attribute_value_types: ["string"],
        },
      }),
    ).toThrow(/align/);
  });

  it("keeps direct id filters out of metric col_type routing", () => {
    const apiFilter = buildApiFilterFromPanelRow({
      field: "trace_id",
      fieldName: "Trace ID",
      fieldType: "string",
      operator: "equals",
      value: "trace-1",
    });

    expect(apiFilter).toEqual({
      column_id: "trace_id",
      display_name: "Trace ID",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "trace-1",
      },
    });
  });

  it("serializes filter UI state to the canonical API wire shape", () => {
    const apiFilter = serializeFilterForApi({
      id: "local-row-id",
      _meta: { parentProperty: "" },
      col_type: "SYSTEM_METRIC",
      column_id: "created_at",
      display_name: "Created at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"],
      },
    });

    expect(apiFilter).toEqual({
      column_id: "created_at",
      display_name: "Created at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"],
      },
    });
    expect(apiFilter).not.toHaveProperty("id");
    expect(apiFilter).not.toHaveProperty("_meta");
    expect(apiFilter).not.toHaveProperty("col_type");
  });

  it("keeps filter-list serialization strict instead of accepting aliases", () => {
    expect(() =>
      serializeFilterListForApi([
        {
          columnId: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "OK",
          },
        },
      ]),
    ).toThrow(/Unknown API filter keys/);
    expect(() =>
      serializeFilterForApi({
        column_id: "status",
        filter_config: {
          filter_type: "text",
          filterOp: "equals",
          filter_value: "OK",
        },
      }),
    ).toThrow(/Unknown API filter_config keys/);
  });

  it("hydrates canonical and legacy stored filters", () => {
    let idCounter = 0;

    expect(
      hydrateStoredFilterList(
        [
          {
            column_id: "status",
            filter_config: {
              filter_type: "text",
              filter_op: "equals",
              filter_value: "OK",
            },
          },
          {
            columnId: "legacy-status",
            displayName: "Legacy Status",
            filterConfig: {
              filterType: "text",
              filterOp: "is",
              filterValue: "OK",
              colType: "SYSTEM_METRIC",
            },
          },
        ],
        () => `generated-${++idCounter}`,
      ),
    ).toEqual([
      {
        id: "generated-1",
        column_id: "status",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "OK",
        },
      },
      {
        id: "generated-2",
        column_id: "legacy-status",
        display_name: "Legacy Status",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "OK",
        },
      },
    ]);
  });

  it("drops unsupported stored filters after attempting legacy upgrade", () => {
    expect(
      hydrateStoredFilterList([
        {
          columnId: "bad-status",
          unexpected: true,
          filterConfig: {
            filterType: "text",
            filterOp: "equals",
            filterValue: "OK",
          },
        },
      ]),
    ).toEqual([]);
  });

  it("drops empty UI draft filters before sending the filter list", () => {
    expect(
      serializeFilterListForApi([
        {
          column_id: "",
          id: "draft-row",
          _meta: { parentProperty: "" },
          filter_config: {
            filter_type: "",
            filter_op: "",
            filter_value: "",
          },
        },
        {
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "OK",
          },
        },
      ]),
    ).toEqual([
      {
        column_id: "status",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "OK",
        },
      },
    ]);
  });

  it("keeps the API contract explicit per type", () => {
    expect(normalizeFilterType("string")).toBe("text");
    expect(isAllowedFilterOperator("number", "contains")).toBe(false);
    expect(isAllowedFilterOperator("number", "not_between")).toBe(true);
    expect(STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS.map).toEqual([
      "equals",
      "not_equals",
      "contains",
      "not_contains",
      "is_null",
      "is_not_null",
    ]);
    expect(isAllowedFilterOperator("map", "contains")).toBe(true);
    expect(isAllowedFilterOperator("map", "between")).toBe(false);
  });

  it("keeps json lists as arrays and canonicalizes json objects to maps", () => {
    expect(normalizeFilterType("json", ["vip"])).toBe("array");
    expect(normalizeFilterType("list", ["vip"])).toBe("array");
    expect(normalizeFilterType("json", { tier: "vip" })).toBe("map");
    expect(normalizeFilterType("map", { tier: "vip" })).toBe("map");
    expect(normalizeFilterType("object", { tier: "vip" })).toBe("map");

    expect(
      serializeFilterForApi({
        column_id: "customer.context",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "json",
          filter_op: "contains",
          filter_value: { tier: "vip", attempt: 2 },
        },
      }),
    ).toEqual({
      column_id: "customer.context",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "map",
        filter_op: "contains",
        filter_value: { tier: "vip", attempt: 2 },
      },
    });
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
      } else if (filterType === "array") {
        expect(output).toEqual(["alpha", "beta"]);
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
