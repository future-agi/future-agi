import { describe, expect, it, vi } from "vitest";
import {
  buildConditionsForRule,
  isScopeReady,
  ruleConditionsToFilters,
} from "../create-rule-dialog";

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  extractErrorMessage: (_error, fallback) => fallback,
  useCreateAutomationRule: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: () => ({}),
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: null, isLoading: false }),
}));

describe("create rule Observe filter serialization", () => {
  it("preserves trace Observe filter col_type values and voice scope", () => {
    const filters = [
      {
        id: "attr",
        columnId: "customer_tier",
        displayName: "Customer Tier",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "vip",
          col_type: "SPAN_ATTRIBUTE",
        },
      },
      {
        id: "eval",
        columnId: "quality_eval",
        displayName: "Quality Eval",
        filterConfig: {
          filterType: "number",
          filterOp: "greater_than_or_equal",
          filterValue: 80,
          col_type: "EVAL_METRIC",
        },
      },
      {
        id: "annotation",
        columnId: "quality_label",
        displayName: "Quality Label",
        filterConfig: {
          filterType: "number",
          filterOp: "between",
          filterValue: [70, 100],
          col_type: "ANNOTATION",
        },
      },
    ];

    const conditions = buildConditionsForRule(
      "trace",
      filters,
      {
        project_id: "project-2",
        is_voice_call: true,
        remove_simulation_calls: true,
      },
      { project: "project-1" },
    );

    expect(conditions.scope).toEqual({
      project_id: "project-2",
      is_voice_call: true,
      remove_simulation_calls: true,
    });
    expect(conditions.filter).toEqual([
      {
        column_id: "customer_tier",
        display_name: "Customer Tier",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "vip",
          col_type: "SPAN_ATTRIBUTE",
        },
      },
      {
        column_id: "quality_eval",
        display_name: "Quality Eval",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than_or_equal",
          filter_value: 80,
          col_type: "EVAL_METRIC",
        },
      },
      {
        column_id: "quality_label",
        display_name: "Quality Label",
        filter_config: {
          filter_type: "number",
          filter_op: "between",
          filter_value: [70, 100],
          col_type: "ANNOTATION",
        },
      },
    ]);
  });

  it("round-trips saved rule filters back into editable UI rows", () => {
    const filters = ruleConditionsToFilters({
      source_type: "trace",
      conditions: {
        filter: [
          {
            column_id: "quality_eval",
            display_name: "Quality Eval",
            filter_config: {
              filter_type: "number",
              filter_op: "greater_than",
              filter_value: 80,
              col_type: "EVAL_METRIC",
            },
          },
        ],
      },
    });

    expect(filters).toHaveLength(1);
    expect(filters[0]).toMatchObject({
      columnId: "quality_eval",
      displayName: "Quality Eval",
      filterConfig: {
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 80,
        col_type: "EVAL_METRIC",
      },
    });
    expect(filters[0].id).toBeTruthy();
  });

  it("uses queue project scope for span and session rules when no override is set", () => {
    const filters = [
      {
        id: "span-name",
        columnId: "span_name",
        filterConfig: {
          filterType: "text",
          filterOp: "contains",
          filterValue: "tool",
          col_type: "SYSTEM_METRIC",
        },
      },
    ];

    expect(
      buildConditionsForRule(
        "observation_span",
        filters,
        {},
        { project: { id: "project-1" } },
      ).scope,
    ).toEqual({ project_id: "project-1" });
    expect(
      buildConditionsForRule(
        "trace_session",
        filters,
        {},
        { project: "project-1" },
      ).scope,
    ).toEqual({ project_id: "project-1" });
  });

  it("uses agent definition scope for simulation rules", () => {
    const filters = [
      {
        id: "status",
        columnId: "status",
        filterConfig: {
          filterType: "categorical",
          filterOp: "equals",
          filterValue: "completed",
        },
      },
    ];

    expect(
      buildConditionsForRule(
        "call_execution",
        filters,
        {},
        { agent_definition: { id: "agent-1" } },
      ).scope,
    ).toEqual({ project_id: "agent-1" });
    expect(isScopeReady("call_execution", {}, {})).toBe(false);
    expect(
      isScopeReady(
        "call_execution",
        {},
        { agent_definition: { id: "agent-1" } },
      ),
    ).toBe(true);
  });
});
