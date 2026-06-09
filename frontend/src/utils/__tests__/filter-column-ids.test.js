import { describe, expect, it } from "vitest";
import {
  canonicalizeApiFilterColumnIds,
  canonicalizeSystemMetricColumnId,
} from "../filter-column-ids";

describe("filter column id canonicalization", () => {
  it("maps frontend system metric aliases to backend column ids", () => {
    expect(canonicalizeSystemMetricColumnId("latency", "SYSTEM_METRIC")).toBe(
      "latency_ms",
    );
    expect(canonicalizeSystemMetricColumnId("tokens", "SYSTEM_METRIC")).toBe(
      "total_tokens",
    );
    expect(
      canonicalizeSystemMetricColumnId("input_tokens", "SYSTEM_METRIC"),
    ).toBe("prompt_tokens");
    expect(
      canonicalizeSystemMetricColumnId("output_tokens", "SYSTEM_METRIC"),
    ).toBe("completion_tokens");
    expect(canonicalizeSystemMetricColumnId("avg_cost", "SYSTEM_METRIC")).toBe(
      "cost",
    );
    expect(
      canonicalizeSystemMetricColumnId("avg_latency", "SYSTEM_METRIC"),
    ).toBe("latency_ms");
  });

  it("canonicalizes snake-case API filter payloads at the wire boundary", () => {
    const filters = canonicalizeApiFilterColumnIds([
      {
        column_id: "latency",
        filter_config: { col_type: "SYSTEM_METRIC", filter_op: "between" },
      },
      {
        column_id: "tokens",
        filter_config: { col_type: "SYSTEM_METRIC", filter_op: "greater_than" },
      },
    ]);

    expect(filters.map((filter) => filter.column_id)).toEqual([
      "latency_ms",
      "total_tokens",
    ]);
  });

  it("does not rewrite non-system columns with the same names", () => {
    const filters = canonicalizeApiFilterColumnIds([
      {
        column_id: "latency",
        filter_config: { col_type: "SPAN_ATTRIBUTE", filter_op: "equals" },
      },
      {
        column_id: "tokens",
        filter_config: { col_type: "ANNOTATION", filter_op: "equals" },
      },
    ]);

    expect(filters.map((filter) => filter.column_id)).toEqual([
      "latency",
      "tokens",
    ]);
  });

  it("handles older saved filters that did not persist col_type", () => {
    const filters = canonicalizeApiFilterColumnIds([
      { column_id: "latency", filter_config: { filter_op: "less_than" } },
    ]);

    expect(filters[0].column_id).toBe("latency_ms");
  });

  it("supports camelCase filter objects before they are snake-cased", () => {
    const filters = canonicalizeApiFilterColumnIds([
      {
        columnId: "output_tokens",
        filterConfig: { colType: "SYSTEM_METRIC", filterOp: "greater_than" },
      },
    ]);

    expect(filters[0].columnId).toBe("completion_tokens");
  });
});
