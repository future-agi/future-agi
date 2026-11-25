import { describe, expect, it } from "vitest";
import { supportsUsageSort, USAGE_SORT_COLUMNS } from "../usageSort";

const filter = (column, family = "SYSTEM_METRIC") => ({
  column_id: column,
  filter_config: { col_type: family },
});

describe("Observe usage sort scope", () => {
  it("permits one project's canonical usage and time predicates", () => {
    expect(supportsUsageSort("project", [])).toBe(true);
    expect(
      supportsUsageSort("project", [
        ...USAGE_SORT_COLUMNS.map((column) => filter(column)),
        filter("created_at"),
        filter("start_time"),
      ]),
    ).toBe(true);
  });
  it("does not send a sort when switching into unsupported filter journeys", () => {
    expect(supportsUsageSort(null, [])).toBe(false);
    for (const item of [
      filter("session_id"),
      filter("end_user_id"),
      filter("has_eval"),
      filter("cost", "SPAN_ATTRIBUTE"),
      filter("created_at", "ANNOTATION"),
      filter("score", "EVAL_METRIC"),
    ])
      expect(supportsUsageSort("project", [item])).toBe(false);
  });
});
