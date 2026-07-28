import { describe, it, expect } from "vitest";
import { buildUsersRequestFilters } from "../common";

const createdAt = (start, end) => ({
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: [start, end],
  },
});

const textFilter = (value) => ({
  column_id: "user_id",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: value,
  },
});

const countCreatedAt = (filters) =>
  filters.filter((f) => f.column_id === "created_at").length;

describe("buildUsersRequestFilters (TH-6081)", () => {
  it("passes a single picked created_at through as exactly one created_at", () => {
    const out = buildUsersRequestFilters([
      createdAt("2026-06-22T00:00:00.000Z", "2026-06-23T00:00:00.000Z"),
    ]);
    expect(countCreatedAt(out)).toBe(1);
  });

  it("does not inject a default created_at when the user picked no date", () => {
    // Regression guard: the old projectFilter appended a 2nd 90-day created_at
    // here, which (backend keeps the last) silently overrode the picked range.
    const out = buildUsersRequestFilters([textFilter("u-1")]);
    expect(countCreatedAt(out)).toBe(0);
  });

  it("returns an empty list for empty/undefined input — no injected date", () => {
    expect(buildUsersRequestFilters([])).toEqual([]);
    expect(buildUsersRequestFilters(undefined)).toEqual([]);
  });

  it("keeps a single created_at alongside other filters (no duplication)", () => {
    const out = buildUsersRequestFilters([
      textFilter("u-1"),
      createdAt("2026-06-22T00:00:00.000Z", "2026-06-23T00:00:00.000Z"),
    ]);
    expect(out).toHaveLength(2);
    expect(countCreatedAt(out)).toBe(1);
  });
});
