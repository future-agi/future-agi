import { describe, expect, it } from "vitest";

import { getComplexFilterValidation } from "../common";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import {
  FILTER_COLUMN_TYPES,
  FILTER_TYPE_ALLOWED_OPS,
  STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS,
} from "src/api/contracts/filter-contract.generated";

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

  it("accepts canonical scalar number filters from URL and persisted views", () => {
    const schema = getComplexFilterValidation();
    const parsed = schema.safeParse({
      column_id: "latency",
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 1,
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.filter_config.filter_value).toBe(1);
  });

  it("accepts canonical scalar datetime filters from URL and persisted views", () => {
    const schema = getComplexFilterValidation();
    const parsed = schema.safeParse({
      column_id: "created_at",
      _meta: { parentProperty: "System Metrics" },
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "datetime",
        filter_op: "greater_than",
        filter_value: "2026-05-13T18:30:00.000Z",
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.filter_config.filter_value).toMatch(/\.000Z$/);
  });

  it("validates every generated filter type instead of a local subset", () => {
    const schema = getComplexFilterValidation();

    for (const filterType of Object.keys(FILTER_TYPE_ALLOWED_OPS)) {
      const parsed = schema.safeParse({
        column_id: `${filterType}_field`,
        _meta: { parentProperty: "System Metrics" },
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: filterType,
          filter_op: "is_null",
        },
      });

      expect(parsed.success, filterType).toBe(true);
      expect(parsed.data.filter_config.filter_value).toBeNull();
    }
  });

  it("accepts flat structured map filters and rejects nested values", () => {
    const schema = getComplexFilterValidation();
    const base = {
      column_id: "customer.context",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "map",
        filter_op: "contains",
      },
    };

    expect(STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS.map).toContain("contains");
    const valid = schema.safeParse({
      ...base,
      filter_config: {
        ...base.filter_config,
        filter_value: { tier: "vip", attempt: 2, accepted: true },
      },
    });
    expect(valid.success).toBe(true);
    expect(valid.data.filter_config.filter_value).toEqual({
      tier: "vip",
      attempt: 2,
      accepted: true,
    });

    const nested = schema.safeParse({
      ...base,
      filter_config: {
        ...base.filter_config,
        filter_value: { nested: { tier: "vip" } },
      },
    });
    expect(nested.success).toBe(false);
  });

  it("validates every generated column type instead of a local subset", () => {
    const schema = getComplexFilterValidation();

    for (const colType of FILTER_COLUMN_TYPES) {
      const parsed = schema.safeParse({
        column_id: `${colType.toLowerCase()}_field`,
        _meta: { parentProperty: "System Metrics" },
        filter_config: {
          col_type: colType,
          filter_type: "text",
          filter_op: "equals",
          filter_value: "ok",
        },
      });

      expect(parsed.success, colType).toBe(true);
    }
  });

  it("preserves aligned mixed scalar provenance for span-attribute lists", () => {
    const schema = getComplexFilterValidation();
    const parsed = schema.safeParse({
      column_id: "mixed.value",
      _meta: { parentProperty: "Attribute" },
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "in",
        filter_value: ["42", 42, false],
        attribute_value_types: ["string", "number", "boolean"],
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.filter_config).toEqual({
      col_type: "SPAN_ATTRIBUTE",
      filter_type: "text",
      filter_op: "in",
      filter_value: ["42", 42, false],
      attribute_value_types: ["string", "number", "boolean"],
    });
  });

  it.each([
    {
      label: "misaligned",
      config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "in",
        filter_value: ["42", 42],
        attribute_value_types: ["string"],
      },
    },
    {
      label: "scalar",
      config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "number",
        filter_op: "equals",
        filter_value: 42,
        attribute_value_types: ["number"],
      },
    },
    {
      label: "non-attribute",
      config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "text",
        filter_op: "in",
        filter_value: ["42"],
        attribute_value_types: ["string"],
      },
    },
  ])("rejects $label attribute-value provenance", ({ config }) => {
    const parsed = getComplexFilterValidation().safeParse({
      column_id: "mixed.value",
      filter_config: config,
    });
    expect(parsed.success).toBe(false);
  });
});
