import { describe, expect, it } from "vitest";
import { supportsRuntimeLatencySort, supportsUsageSort, USAGE_SORT_COLUMNS } from "../usageSort";

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

const latency = { column_id: "latency", direction: "desc" };
const date = { column_id: "created_at", filter_config: { filter_type: "datetime", filter_op: "between", filter_value: ["2026-11-01", "2026-11-02"] } };
const set = { column_id: "skill.trace_cohort", filter_config: { col_type: "SKILL", filter_type: "text", filter_op: "equals", filter_value: "b".repeat(64) } };

it("only enables the Runtime order for the complete supported filter shape", () => {
  expect(supportsRuntimeLatencySort("project", latency, [date, set])).toBe(true);
  expect(supportsRuntimeLatencySort(null, latency, [date, set])).toBe(false);
  expect(supportsRuntimeLatencySort("project", { ...latency, direction: "asc" }, [date, set])).toBe(false);
  for (const filters of [[date], [set, set], [date, set, filter("cost")], [date, { ...set, group: 1 }],
    [date, filter("skill.trace_cohort", "SPAN_ATTRIBUTE")], [set, { ...date, source: "EVAL_METRIC" }]]) {
    expect(supportsRuntimeLatencySort("project", latency, filters)).toBe(false);
  }
});
