import { describe, expect, it } from "vitest";

import {
  avoidDuplicateFilterSet,
  filterDefinitionMatchesSelection,
  getComplexFilterValidation,
  getFilterDefinitionIdentity,
  getFilterDefinitionSelectionValue,
  getFilterUsageCounts,
  isFilterDefinitionAtMaxUsage,
  stripUiFilterKeys,
} from "../common";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import {
  FILTER_COLUMN_TYPES,
  FILTER_TYPE_ALLOWED_OPS,
  STRUCTURED_SPAN_ATTRIBUTE_ALLOWED_OPS,
} from "src/api/contracts/filter-contract.generated";

describe("ComplexFilter contract wiring", () => {
  it("keeps registry identity separate from the native filter column", () => {
    expect(
      getFilterDefinitionIdentity({
        propertyId: "model",
        registryId: "custom_attribute:model",
      }),
    ).toEqual({
      column_id: "model",
      registryId: "custom_attribute:model",
    });

    const parsed = getComplexFilterValidation().parse({
      column_id: "model",
      registryId: "custom_attribute:model",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "equals",
        filter_value: "gpt-4o",
      },
    });
    expect(parsed).toMatchObject({
      column_id: "model",
      property_id: "custom_attribute:model",
    });
    expect(stripUiFilterKeys([{ ...parsed, registryId: "ignored" }])).toEqual([
      parsed,
    ]);
  });

  it("uses registry identity for selection and keeps a legacy fallback", () => {
    const systemModel = {
      propertyId: "model",
      registryId: "system_attribute:traces:model",
      propertyName: "Model",
    };
    const customModel = {
      propertyId: "model",
      registryId: "custom_attribute:model",
      propertyName: "Model attribute",
    };

    expect(getFilterDefinitionSelectionValue(systemModel)).not.toBe(
      getFilterDefinitionSelectionValue(customModel),
    );
    expect(
      filterDefinitionMatchesSelection(
        customModel,
        "system_attribute:traces:model",
      ),
    ).toBe(false);
    expect(filterDefinitionMatchesSelection(customModel, "model")).toBe(true);
  });

  it("keeps choices of one registry property independently selectable", () => {
    const approved = {
      propertyId: "review-label**approved",
      registryId: "annotation:review-label",
      propertyName: "Approved",
    };
    const rejected = {
      propertyId: "review-label**rejected",
      registryId: "annotation:review-label",
      propertyName: "Rejected",
    };

    const approvedSelection = getFilterDefinitionSelectionValue(approved);
    const rejectedSelection = getFilterDefinitionSelectionValue(rejected);
    expect(approvedSelection).not.toBe(rejectedSelection);
    expect(filterDefinitionMatchesSelection(approved, approvedSelection)).toBe(
      true,
    );
    expect(filterDefinitionMatchesSelection(rejected, approvedSelection)).toBe(
      false,
    );
  });

  it("applies maxUsage per registry identity for same-name properties", () => {
    const systemModel = {
      propertyId: "model",
      registryId: "system_attribute:traces:model",
      maxUsage: 1,
    };
    const customModel = {
      propertyId: "model",
      registryId: "custom_attribute:model",
      maxUsage: 1,
    };
    const selectedSystem = {
      column_id: "model",
      registryId: "system_attribute:traces:model",
    };
    const counts = getFilterUsageCounts([selectedSystem]);

    expect(
      isFilterDefinitionAtMaxUsage(systemModel, counts, { column_id: "" }),
    ).toBe(true);
    expect(
      isFilterDefinitionAtMaxUsage(customModel, counts, { column_id: "" }),
    ).toBe(false);
    expect(
      isFilterDefinitionAtMaxUsage(systemModel, counts, selectedSystem),
    ).toBe(false);
  });

  it("deduplicates by registry identity with native-column legacy fallback", () => {
    const systemModel = {
      column_id: "model",
      property_id: "system_attribute:traces:model",
      filter_config: { filter_value: "gpt-4.1" },
    };
    const customModel = {
      column_id: "model",
      property_id: "custom_attribute:model",
      filter_config: { filter_value: "customer-model" },
    };

    expect(avoidDuplicateFilterSet([systemModel], customModel)).toEqual([
      systemModel,
      customModel,
    ]);

    const updatedSystem = {
      ...systemModel,
      filter_config: { filter_value: "gpt-4o" },
    };
    expect(
      avoidDuplicateFilterSet([systemModel, customModel], updatedSystem),
    ).toEqual([updatedSystem, customModel]);

    const legacyModel = {
      column_id: "model",
      filter_config: { filter_value: "legacy" },
    };
    expect(avoidDuplicateFilterSet([legacyModel], updatedSystem)).toEqual([
      updatedSystem,
    ]);
  });

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

// Review comment on PR #2064: the reduce replaces on *every* match, so with two
// panel rows on one column a quick-filter click pushes the incoming filter once
// per match. The chips then duplicate, and removing one by index leaves the
// filter applied.
describe("avoidDuplicateFilterSet", () => {
  const row = (column_id, filter_value, id) => ({
    id,
    column_id,
    filter_config: {
      filter_type: "text",
      filter_op: "equals",
      filter_value,
    },
  });

  it("collapses several rows on one column to a single filter", () => {
    const prev = [
      row("provider", "anthropic", "a"),
      row("provider", "openai", "b"),
    ];
    const incoming = row("provider", "google", "c");

    const result = avoidDuplicateFilterSet(prev, incoming);

    expect(result).toEqual([incoming]);
  });

  it("keeps the replacement in the first match's position", () => {
    const prev = [
      row("model", "gpt-4", "m"),
      row("provider", "anthropic", "a"),
      row("status", "OK", "s"),
      row("provider", "openai", "b"),
    ];
    const incoming = row("provider", "google", "c");

    expect(avoidDuplicateFilterSet(prev, incoming)).toEqual([
      prev[0],
      incoming,
      prev[2],
    ]);
  });

  it("replaces a single existing row on that column", () => {
    const prev = [row("provider", "anthropic", "a")];
    const incoming = row("provider", "google", "c");
    expect(avoidDuplicateFilterSet(prev, incoming)).toEqual([incoming]);
  });

  it("appends when no row uses that column", () => {
    const prev = [row("model", "gpt-4", "m")];
    const incoming = row("provider", "google", "c");
    expect(avoidDuplicateFilterSet(prev, incoming)).toEqual([
      prev[0],
      incoming,
    ]);
  });

  it("drops empty draft rows, as before", () => {
    // isEmptyFilter deep-equals this exact shape.
    const empty = {
      id: "e",
      column_id: "",
      filter_config: { filter_type: "", filter_op: "", filter_value: "" },
    };
    const incoming = row("provider", "google", "c");
    expect(avoidDuplicateFilterSet([empty], incoming)).toEqual([incoming]);
  });
});
