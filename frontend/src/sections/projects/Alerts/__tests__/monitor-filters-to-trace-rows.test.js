import { describe, expect, it } from "vitest";

import { getComplexFilterValidation } from "src/components/ComplexFilter/common";

import { monitorFiltersToTraceRows } from "../monitorFiltersToTraceRows";

const MONITOR_FILTERS = {
  observation_type: ["llm", "tool", "agent"],
  span_attributes_filters: [
    {
      column_id: "customer_tier",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_op: "equals",
        filter_type: "text",
        filter_value: "enterprise",
      },
    },
    {
      column_id: "retry_count",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_op: "greater_than",
        filter_type: "number",
        filter_value: 2,
      },
    },
  ],
};

describe("monitorFiltersToTraceRows", () => {
  it("emits one row per span attribute plus one for span type", () => {
    const rows = monitorFiltersToTraceRows(MONITOR_FILTERS);

    expect(rows).toHaveLength(3);
    expect(rows.map((row) => row.column_id).sort()).toEqual([
      "customer_tier",
      "node_type",
      "retry_count",
    ]);
  });

  it("gives every row a unique id", () => {
    const rows = monitorFiltersToTraceRows(MONITOR_FILTERS);

    expect(rows.every((row) => typeof row.id === "string" && row.id)).toBe(true);
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
  });

  it("preserves the span attribute config verbatim, col_type included", () => {
    const tier = monitorFiltersToTraceRows(MONITOR_FILTERS).find(
      (row) => row.column_id === "customer_tier",
    );

    expect(tier.filter_config).toEqual({
      col_type: "SPAN_ATTRIBUTE",
      filter_op: "equals",
      filter_type: "text",
      filter_value: "enterprise",
    });
  });

  // "string" is what the alert panel's SPAN_TYPE_PROPERTY uses, and it is NOT in
  // the filter schema's enum — a row carrying it is dropped without a word.
  it("emits the span type row as text/in, never string", () => {
    const spanType = monitorFiltersToTraceRows(MONITOR_FILTERS).find(
      (row) => row.column_id === "node_type",
    );

    expect(spanType.filter_config).toMatchObject({
      filter_type: "text",
      filter_op: "in",
      filter_value: ["llm", "tool", "agent"],
    });
    expect(spanType.filter_config.filter_type).not.toBe("string");
  });

  it("omits the span type row when the monitor watches every type", () => {
    expect(monitorFiltersToTraceRows({ observation_type: [] })).toEqual([]);
    expect(monitorFiltersToTraceRows({ span_attributes_filters: [] })).toEqual(
      [],
    );
  });

  it("skips malformed span attribute entries", () => {
    const rows = monitorFiltersToTraceRows({
      span_attributes_filters: [
        { column_id: "", filter_config: { filter_op: "equals" } },
        { filter_config: { filter_op: "equals" } },
        { column_id: "orphan" },
        null,
      ],
    });

    expect(rows).toEqual([]);
  });

  it("tolerates a monitor with no filters at all", () => {
    expect(monitorFiltersToTraceRows(null)).toEqual([]);
    expect(monitorFiltersToTraceRows(undefined)).toEqual([]);
    expect(monitorFiltersToTraceRows({})).toEqual([]);
  });

  // The guard that matters: shape assertions above prove nothing if the trace
  // list's own validation rejects the row, because it drops it silently.
  it("produces rows the trace list's own validation accepts", () => {
    const validate = getComplexFilterValidation(true, () => {});

    for (const row of monitorFiltersToTraceRows(MONITOR_FILTERS)) {
      expect(validate.safeParse(row).success, `row ${row.column_id}`).toBe(true);
    }
  });

  // The monitor's stored filters are only half of what the alert measured: each
  // metric appends its own condition in SQL that is persisted nowhere. Without
  // it a "73 errors" alert lands on 89 traces — the 73 plus healthy traffic that
  // matched the filters but never contributed.
  describe("the metric's own condition", () => {
    it("restricts a count-of-errors alert to failed spans", () => {
      const rows = monitorFiltersToTraceRows(MONITOR_FILTERS, {
        metricType: "count_of_errors",
      });
      const status = rows.find((row) => row.column_id === "status");

      expect(status.filter_config).toMatchObject({
        filter_type: "text",
        filter_op: "equals",
        filter_value: "ERROR",
      });
      expect(status.id).toBeTruthy();
    });

    it("adds nothing for a metric with no extra condition", () => {
      const rows = monitorFiltersToTraceRows(MONITOR_FILTERS, {
        metricType: "span_response_time",
      });

      expect(rows.some((row) => row.column_id === "status")).toBe(false);
    });

    it("adds nothing when no metric type is given", () => {
      const rows = monitorFiltersToTraceRows(MONITOR_FILTERS);

      expect(rows.some((row) => row.column_id === "status")).toBe(false);
    });

    it("still emits the metric row for a monitor with no filters of its own", () => {
      const rows = monitorFiltersToTraceRows({}, {
        metricType: "count_of_errors",
      });

      expect(rows.map((row) => row.column_id)).toEqual(["status"]);
    });

    it("produces a metric row the trace list's validation accepts", () => {
      const validate = getComplexFilterValidation(true, () => {});
      const rows = monitorFiltersToTraceRows(MONITOR_FILTERS, {
        metricType: "count_of_errors",
      });

      for (const row of rows) {
        expect(validate.safeParse(row).success, `row ${row.column_id}`).toBe(
          true,
        );
      }
    });
  });
});
