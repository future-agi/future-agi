import { describe, expect, it } from "vitest";
import {
  buildAnnotatorFilterChipLabelMap,
  buildSimulationSelectorColumnDefs,
  buildSimulationSelectorFilterFields,
} from "../items/add-items-dialog";
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

describe("Simulation add-items filters", () => {
  it("exposes the same core simulation filters used by automation rules", () => {
    expect(buildSimulationSelectorFilterFields()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "status",
          name: "Status",
          category: "system",
          type: "categorical",
        }),
        expect.objectContaining({
          id: "simulation_call_type",
          name: "Simulation Call Type",
          category: "system",
          type: "text",
        }),
        expect.objectContaining({
          id: "persona.language",
          name: "Language",
          category: "persona",
          type: "categorical",
          choices: expect.arrayContaining(["English", "Hindi"]),
        }),
        expect.objectContaining({
          id: "persona.communication_style",
          name: "Communication Style",
          category: "persona",
          type: "categorical",
        }),
        expect.objectContaining({
          id: "persona.multilingual",
          name: "Multilingual",
          category: "persona",
          type: "boolean",
        }),
        expect.objectContaining({
          id: "duration_seconds",
          name: "Duration",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "avg_agent_latency_ms",
          name: "Latency",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "cost_cents",
          name: "Cost",
          category: "system",
          type: "number",
        }),
        expect.objectContaining({
          id: "created_at",
          name: "Created At",
          category: "system",
          type: "date",
        }),
      ]),
    );
  });

  it("adds scenario attributes and eval columns from simulation column order", () => {
    const fields = buildSimulationSelectorFilterFields([
      {
        id: "scenario-priority",
        column_name: "Priority",
        data_type: "text",
        type: "scenario_dataset_column",
      },
      {
        id: "scenario-attempts",
        column_name: "Attempts",
        data_type: "integer",
        type: "scenario_dataset_column",
      },
      {
        id: "eval-quality",
        column_name: "Quality Score",
        output_type: "score",
        type: "evaluation",
      },
      {
        id: "tool-eval-status",
        column_name: "Tool Status",
        output_type: "Pass/Fail",
        type: "tool_evaluation",
      },
    ]);

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "scenario-priority",
          name: "Priority",
          category: "attribute",
          type: "text",
        }),
        expect.objectContaining({
          id: "scenario-attempts",
          name: "Attempts",
          category: "attribute",
          type: "number",
        }),
        expect.objectContaining({
          id: "eval-quality",
          name: "Quality Score",
          category: "eval",
          type: "number",
        }),
        expect.objectContaining({
          id: "tool-eval-status",
          name: "Tool Status",
          category: "eval",
          type: "text",
        }),
      ]),
    );
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
          column_id: "total_traces_count",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: "2",
          },
        },
      ],
      { dateFilter: ["2026-01-01", "2026-02-01"] },
    );

    expect(filters).toEqual([
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

describe("Add-items annotator filter chips", () => {
  it("maps selected annotator ids to name and email labels", () => {
    expect(
      buildAnnotatorFilterChipLabelMap([
        {
          value: "e1f8e455-9248-4aec-a510-ead35a946235",
          label: "Kartik",
          email: "kartik.nvj@futureagi.com",
        },
      ]),
    ).toEqual({
      annotator: {
        "e1f8e455-9248-4aec-a510-ead35a946235":
          "Kartik (kartik.nvj@futureagi.com)",
      },
    });
  });
});
