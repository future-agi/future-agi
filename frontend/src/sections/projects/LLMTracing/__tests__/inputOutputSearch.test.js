import { describe, expect, it } from "vitest";
import { hydrateStoredFilterList } from "src/api/contracts/filter-contract";
import { toBackendFilters } from "../common";
import { serializeTraceFiltersForPersistence } from "../filter_persistence";
import {
  inputOutputSearchValue,
  isInputOutputSearchFilter,
  replaceInputOutputSearch,
} from "../inputOutputSearch";

const timestamp = {
  column_id: "created_at",
  filter_config: { filter_type: "datetime", filter_op: "between", filter_value: ["2026-08-08T12:00:00Z", "2026-08-08T13:00:00Z"] },
};
const model = {
  column_id: "model",
  filter_config: { col_type: "SYSTEM_METRIC", filter_type: "text", filter_op: "equals", filter_value: "model-a" },
};

describe("the canonical content search shared by both Observe list requests", () => {
  it("replaces only reserved leaves, preserving date, ordinary filters and an attribute with the same name", () => {
    const attribute = { column_id: "input_output", filter_config: { ...model.filter_config, col_type: "SPAN_ATTRIBUTE" } };
    const previous = replaceInputOutputSearch([timestamp, model, attribute], "old");
    const duplicate = previous.find(isInputOutputSearchFilter);
    const next = replaceInputOutputSearch([...previous, duplicate], "É_%'\\new");
    expect(next.filter(isInputOutputSearchFilter)).toHaveLength(1);
    expect(next.slice(0, 3)).toEqual([timestamp, model, attribute]);
    expect(inputOutputSearchValue(next)).toBe("É_%'\\new");
    expect(inputOutputSearchValue(previous)).toBe("old");
    // Both grids call this real transport boundary with filters + extraFilters.
    expect(toBackendFilters(next).filter(isInputOutputSearchFilter)).toEqual([
      expect.objectContaining({ filter_config: { col_type: "SYSTEM_METRIC", filter_type: "text", filter_op: "contains", filter_value: "É_%'\\new" } }),
    ]);
    expect(replaceInputOutputSearch(next, "  ")).toEqual([timestamp, model, attribute]);
  });

  it("round-trips through saved-view serialization and tab hydration", () => {
    const saved = serializeTraceFiltersForPersistence(replaceInputOutputSearch([timestamp, model], "saved content"));
    const restored = hydrateStoredFilterList(saved, () => "row-id");
    expect(inputOutputSearchValue(restored)).toBe("saved content");
    expect(toBackendFilters(restored)).toEqual(saved);
    expect(inputOutputSearchValue([])).toBe("");
  });
});
