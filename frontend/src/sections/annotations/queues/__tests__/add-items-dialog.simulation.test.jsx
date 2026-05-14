import { describe, expect, it } from "vitest";
import { objectCamelToSnake } from "src/utils/utils";
import { buildSimulationSelectorColumnDefs } from "../items/add-items-dialog";
import {
  buildSessionSelectionFilters,
  buildSessionSelectorFilterFields,
} from "../items/add-items-session-utils";

function valuesByHeader(row, columnOrder = []) {
  return Object.fromEntries(
    buildSimulationSelectorColumnDefs(columnOrder)
      .filter((column) => column.headerName && column.valueGetter)
      .map((column) => [
        column.headerName,
        column.valueGetter({ data: row, value: undefined }),
      ]),
  );
}

describe("Simulation add-items columns", () => {
  it("renders raw serializer metrics for voice simulation rows", () => {
    const values = valuesByHeader({
      duration_seconds: 44,
      response_time_ms: 1250,
      avg_agent_latency_ms: 2742,
      talk_ratio: 0.17994553981140626,
      cost_cents: 89,
    });

    expect(values.Duration).toBe("44s");
    expect(values["Response Time"]).toBeUndefined();
    expect(values.Latency).toBe("2.74s");
    expect(values["Agent Talk (%)"]).toBe("15.3%");
    expect(values.Cost).toBe("$0.89");
  });

  it("renders nested customer metric aliases used by provider payloads", () => {
    const values = valuesByHeader({
      customer_latency_metrics: {
        systemMetrics: {
          responseTimeMs: 980,
          avgAgentLatencyMs: 1794,
          botPct: 28.2,
        },
      },
      customer_cost_breakdown: {
        total: 0.1234,
      },
    });

    expect(values["Response Time"]).toBeUndefined();
    expect(values.Latency).toBe("1.79s");
    expect(values["Agent Talk (%)"]).toBe("28.2%");
    expect(values.Cost).toBe("$0.1234");
  });

  it("deduplicates visible legacy metric column ids from execution column order", () => {
    const columns = buildSimulationSelectorColumnDefs([
      { id: "avg_agent_latency_ms", column_name: "Average Latency (ms)" },
      { id: "latency_ms", column_name: "Latency (ms)" },
      { id: "customer_cost_cents", column_name: "Customer Cost" },
      { id: "cost", column_name: "Cost" },
      { id: "response_time_ms", column_name: "Response Time (ms)" },
      { id: "avg_response_time_ms", column_name: "Average Response Time" },
    ]);

    expect(
      columns.filter((column) => column.headerName === "Latency"),
    ).toHaveLength(1);
    expect(
      columns.filter((column) => column.headerName === "Cost"),
    ).toHaveLength(1);
  });

  it("hides response-time aliases because voice observability does not show it", () => {
    const columns = buildSimulationSelectorColumnDefs([
      { id: "response_time_ms", column_name: "Response Time (ms)" },
      { id: "avg_response_time_ms", column_name: "Average Response Time" },
      { id: "responseTimeMs", column_name: "Response Time" },
    ]);

    expect(
      columns.filter((column) => column.headerName === "Response Time"),
    ).toHaveLength(0);
    expect(
      columns.filter(
        (column) =>
          column.colId === "response_time" ||
          column.colId === "response_time_ms" ||
          column.colId === "avg_response_time_ms" ||
          column.colId === "responseTimeMs",
      ),
    ).toHaveLength(0);
  });

  it("keeps agent talk blank when no direct value or ratio exists", () => {
    const values = valuesByHeader({});

    expect(values["Agent Talk (%)"]).toBe("-");
  });
});

describe("Session add-items filters", () => {
  it("maps session fields to the searchable filter panel shape", () => {
    const fields = buildSessionSelectorFilterFields([
      {
        id: "annotation_quality",
        name: "Annotation Quality",
        groupBy: "Annotation Metrics",
        dataType: "number",
      },
    ]);

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "session_id",
          name: "Session ID",
          category: "system",
          type: "string",
        }),
        expect.objectContaining({
          id: "start_time",
          name: "Start Time",
          category: "system",
          type: "datetime",
        }),
        expect.objectContaining({
          id: "annotation_quality",
          name: "Annotation Quality",
          category: "annotation",
          type: "number",
        }),
      ]),
    );
  });

  it("adds the date-range filter in the API payload shape used by list sessions", () => {
    const filters = buildSessionSelectionFilters(
      [
        {
          columnId: "total_traces_count",
          filterConfig: {
            filterType: "number",
            filterOp: "greater_than",
            filterValue: "2",
          },
        },
      ],
      { dateFilter: ["2026-01-01", "2026-02-01"] },
    );

    expect(objectCamelToSnake(filters)).toEqual([
      {
        column_id: "total_traces_count",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: "2",
        },
      },
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-01-01T00:00:00.000Z",
            "2026-02-01T00:00:00.000Z",
          ],
        },
      },
    ]);
  });
});
