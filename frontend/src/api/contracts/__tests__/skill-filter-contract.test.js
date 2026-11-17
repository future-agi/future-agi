import { describe, expect, it } from "vitest";
import { hydrateStoredFilterList, serializeFilterForApi, serializeFilterListForApi } from "../filter-contract";
import { serializeSavedViewConfig } from "src/api/project/saved-views";

const skill = (column, value, op = "equals", group = 1) => ({
  column_id: column,
  group,
  filter_config: {
    col_type: "SKILL",
    filter_type: column === "skill.version" ? "number" : "text",
    filter_op: op,
    filter_value: value,
  },
});

describe("shared SKILL filter transport", () => {
  it("round-trips independent load groups without merging their row identities", () => {
    const filters = [skill("skill.id", "skill-a", "equals", 0), skill("skill.id", "skill-b"), skill("skill.agent", "", "equals", 1)];
    expect(serializeFilterListForApi(filters)).toEqual(filters);
    expect(serializeSavedViewConfig({ filters, compare_filters: filters })).toEqual({ filters, compare_filters: filters });
    expect(serializeFilterListForApi(hydrateStoredFilterList(filters))).toEqual(filters);
  });

  it.each(["gte", "lte", "in"])("retains the version %s operator for saved views", (op) => {
    const item = skill("skill.version", op === "in" ? [2, 3] : 3, op);
    expect(serializeFilterForApi(item)).toEqual(item);
  });

  it("preserves unnamed agents in membership filters", () => {
    const item = skill("skill.agent", ["", "support"], "in");
    expect(serializeFilterForApi(item)).toEqual(item);
  });

  it.each([-1, 2147483648, 1.5, "1", true])("rejects invalid group %s", (group) => {
    expect(() => serializeFilterForApi(skill("skill.id", "skill-a", "equals", group))).toThrow(/group/);
  });

  it("does not widen non-skill filter contracts", () => {
    const item = skill("latency", 3, "gte");
    item.filter_config.col_type = "SYSTEM_METRIC";
    expect(() => serializeFilterForApi(item)).toThrow(/group/);
    delete item.group;
    expect(() => serializeFilterForApi(item)).toThrow(/Unsupported filter operator/);
  });
});
