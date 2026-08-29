import { describe, expect, it } from "vitest";

import { avoidDuplicateFilterSet, getComplexFilterValidation } from "../common";
import { AdvanceNumberFilterOperators } from "src/utils/constants";
import {
  FILTER_COLUMN_TYPES,
  FILTER_TYPE_ALLOWED_OPS,
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
