import { describe, expect, it } from "vitest";

import { hydrateStoredFilterList } from "src/api/contracts/filter-contract";
import { serializeTraceFiltersForPersistence } from "../filter_persistence";
import { hydrateFiltersWithSkills } from "../skillFilters";

const V3 = "33333333-3333-4333-8333-333333333333";
const SKILL = "11111111-1111-4111-8111-111111111111";
const skillItem = (column, value, extra = {}) => ({
  column_id: column,
  ...extra,
  filter_config: {
    col_type: "SKILL",
    filter_type: "text",
    filter_op: "equals",
    filter_value: value,
  },
});

describe("trace filter persistence", () => {
  it.each(["primary", "compare"])(
    "preserves registry identity for %s filters",
    () => {
      const stored = serializeTraceFiltersForPersistence([
        {
          id: "ui-row",
          registryId: "custom_attribute:model",
          column_id: "model",
          _meta: { parentProperty: "Attribute" },
          filter_config: {
            col_type: "SPAN_ATTRIBUTE",
            filter_type: "text",
            filter_op: "equals",
            filter_value: "tenant-model",
          },
        },
      ]);

      expect(stored).toEqual([
        {
          column_id: "model",
          property_id: "custom_attribute:model",
          filter_config: {
            col_type: "SPAN_ATTRIBUTE",
            filter_type: "text",
            filter_op: "equals",
            filter_value: "tenant-model",
          },
        },
      ]);
      expect(hydrateStoredFilterList(stored)).toEqual(stored);
    },
  );

  it("round-trips a single Skill chip through the shared serializer", () => {
    const stored = serializeTraceFiltersForPersistence([
      { id: "row", ...skillItem("skill.version_id", V3) },
    ]);
    expect(stored).toEqual([skillItem("skill.version_id", V3)]);
    expect(
      hydrateFiltersWithSkills(stored, {
        hydrateOthers: hydrateStoredFilterList,
      }).rows,
    ).toEqual(stored);
  });

  it("keeps the correlation group of a second skill", () => {
    const latency = {
      column_id: "latency",
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 100,
      },
    };
    const stored = serializeTraceFiltersForPersistence([
      skillItem("skill.version_id", V3),
      { id: "row", ...skillItem("skill.id", SKILL, { group: 1 }) },
      latency,
    ]);
    expect(stored).toEqual([
      latency,
      skillItem("skill.version_id", V3),
      { ...skillItem("skill.id", SKILL), group: 1 },
    ]);
    expect(
      hydrateFiltersWithSkills(stored, {
        hydrateOthers: hydrateStoredFilterList,
      }).rows.map((row) => [row.column_id, row.group]),
    ).toEqual([
      ["latency", undefined],
      ["skill.version_id", undefined],
      ["skill.id", 1],
    ]);
  });
});
