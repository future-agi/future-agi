import { describe, expect, it } from "vitest";
import {
  buildConditionsForRule,
  getTraceRulePanelMode,
  isScopeReady,
  removeSubmittableFilterAtIndex,
  ruleConditionsToFilters,
} from "../../utils/automation-rule-utils";

describe("create rule Observe filter serialization", () => {
  it("uses the same trace, voice, span, and session panel modes as Observe", () => {
    expect(getTraceRulePanelMode("trace")).toEqual({
      source: "traces",
      tab: "trace",
      isSpansView: false,
    });
    expect(getTraceRulePanelMode("trace", { isVoiceProject: true })).toEqual({
      source: "traces",
      tab: "voiceCalls",
      isSpansView: false,
    });
    expect(getTraceRulePanelMode("observation_span")).toEqual({
      source: "traces",
      tab: "spans",
      isSpansView: true,
    });
    expect(getTraceRulePanelMode("trace_session")).toEqual({
      source: "sessions",
      tab: undefined,
      isSpansView: false,
    });
  });

  it("removes only the selected duplicate chip", () => {
    const duplicate = {
      column_id: "status",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: ["OK"],
      },
    };
    const filters = [
      { ...duplicate, id: "first" },
      { id: "draft", column_id: "", filter_config: {} },
      {
        ...duplicate,
        id: "second",
        filter_config: { ...duplicate.filter_config, filter_value: ["ERROR"] },
      },
    ];

    expect(
      removeSubmittableFilterAtIndex(filters, 1).map((row) => row.id),
    ).toEqual(["first", "draft"]);
  });

  it("preserves trace Observe filter col_type values and voice scope", () => {
    const filters = [
      {
        id: "attr",
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
        id: "eval",
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
        id: "annotation",
        column_id: "quality_label",
        display_name: "Quality Label",
        filter_config: {
          filter_type: "number",
          filter_op: "between",
          filter_value: [70, 100],
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
      {},
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
      column_id: "quality_eval",
      display_name: "Quality Eval",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 80,
        col_type: "EVAL_METRIC",
      },
    });
    expect(filters[0].id).toBeTruthy();
  });

  it("preserves annotation property identity when a saved rule is edited", () => {
    const savedFilter = {
      column_id: "quality_label",
      property_id: "annotation:quality-label-id",
      display_name: "Quality Label",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["Passed"],
        col_type: "ANNOTATION",
      },
    };

    const [editableFilter] = ruleConditionsToFilters({
      source_type: "trace",
      conditions: { filter: [savedFilter] },
    });
    expect(editableFilter.property_id).toBe("annotation:quality-label-id");

    const conditions = buildConditionsForRule(
      "trace",
      [editableFilter],
      { project_id: "project-1" },
      {},
    );
    expect(conditions.filter).toEqual([savedFilter]);
  });

  it("round-trips mixed typed attribute values without changing their storage family", () => {
    const savedFilter = {
      column_id: "mixed_attribute",
      display_name: "Mixed Attribute",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: ["vip", 2, true],
        col_type: "SPAN_ATTRIBUTE",
        attribute_value_types: ["string", "number", "boolean"],
      },
    };

    const [editableFilter] = ruleConditionsToFilters({
      source_type: "trace",
      conditions: { filter: [savedFilter] },
    });
    const conditions = buildConditionsForRule(
      "trace",
      [editableFilter],
      { project_id: "project-1" },
      {},
    );

    expect(conditions.filter).toEqual([savedFilter]);
  });

  it("uses queue project scope for span and session rules when no override is set", () => {
    const filters = [
      {
        id: "span-name",
        column_id: "span_name",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "tool",
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
        column_id: "status",
        filter_config: {
          filter_type: "categorical",
          filter_op: "equals",
          filter_value: "completed",
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

  it("stores canonical system metric ids for trace rule filters", () => {
    const conditions = buildConditionsForRule(
      "trace",
      [
        {
          id: "latency-ms",
          column_id: "latency_ms",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 500,
            col_type: "SYSTEM_METRIC",
          },
        },
      ],
      { project_id: "project-1" },
      {},
    );

    expect(conditions).not.toHaveProperty("rules");
    expect(conditions.filter[0].column_id).toBe("latency_ms");
  });

  it("keeps non-default queue-bound scope authoritative over stale picker values", () => {
    const filters = [
      {
        id: "status",
        column_id: "status",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["OK"],
          col_type: "SYSTEM_METRIC",
        },
      },
    ];

    expect(
      buildConditionsForRule(
        "trace",
        filters,
        { project_id: "wrong-project", is_voice_call: true },
        { project: { id: "queue-project" }, is_default: false },
      ).scope,
    ).toEqual({
      project_id: "queue-project",
      is_voice_call: true,
      remove_simulation_calls: false,
    });

    expect(
      buildConditionsForRule(
        "dataset_row",
        [],
        { dataset_id: "wrong-dataset" },
        { dataset: { id: "queue-dataset" }, is_default: false },
      ).scope,
    ).toEqual({ dataset_id: "queue-dataset" });

    expect(
      buildConditionsForRule(
        "call_execution",
        filters,
        { project_id: "wrong-agent" },
        { agent_definition: { id: "queue-agent" }, is_default: false },
      ).scope,
    ).toEqual({ project_id: "queue-agent" });
  });

  it("lets default queues target the selected source scope", () => {
    const filters = [
      {
        id: "status",
        column_id: "status",
        filter_config: {
          filter_type: "categorical",
          filter_op: "in",
          filter_value: ["OK"],
          col_type: "SYSTEM_METRIC",
        },
      },
    ];

    expect(
      buildConditionsForRule(
        "trace",
        filters,
        { project_id: "selected-project", is_voice_call: true },
        { project: { id: "queue-project" }, is_default: true },
      ).scope,
    ).toEqual({
      project_id: "selected-project",
      is_voice_call: true,
      remove_simulation_calls: false,
    });

    expect(
      buildConditionsForRule(
        "dataset_row",
        [],
        { dataset_id: "selected-dataset" },
        { dataset: { id: "queue-dataset" }, is_default: true },
      ).scope,
    ).toEqual({ dataset_id: "selected-dataset" });

    expect(
      buildConditionsForRule(
        "call_execution",
        filters,
        { project_id: "selected-agent" },
        { agent_definition: { id: "queue-agent" }, is_default: true },
      ).scope,
    ).toEqual({ project_id: "selected-agent" });
  });
});
