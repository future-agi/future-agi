import { describe, expect, it } from "vitest";
import { buildSimulationSelectorColumnDefs } from "../items/add-items-dialog";

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
