import { describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  const values = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: (key) => values.delete(key),
      clear: () => values.clear(),
    },
  });
});

import { buildFilterDefinitions, normalizeFilters } from "./common";

describe("prompt metric property registry definitions", () => {
  it("retains the registry identity separately from the native column id", () => {
    const [definition] = buildFilterDefinitions([
      {
        id: "prompt_template_version",
        name: "Versions",
        property_id: "system_attribute:prompts:prompt_template_version",
        property_kind: "system_attribute",
        property_source: "prompts",
      },
    ]);

    expect(definition).toMatchObject({
      propertyId: "prompt_template_version",
      registryId: "system_attribute:prompts:prompt_template_version",
      propertyKind: "system_attribute",
      propertySource: "prompts",
    });
  });

  it("keeps one eval-config identity for choice-specific native columns", () => {
    const definitions = buildFilterDefinitions([
      {
        id: "config-id**good",
        name: "Good (Quality)",
        property_id: "eval_config:config-id",
        property_kind: "eval_config",
        property_source: "traces",
      },
      {
        id: "config-id**bad",
        name: "Bad (Quality)",
        property_id: "eval_config:config-id",
        property_kind: "eval_config",
        property_source: "traces",
      },
    ]);

    expect(definitions.map((definition) => definition.propertyId)).toEqual([
      "config-id**good",
      "config-id**bad",
    ]);
    expect(definitions.map((definition) => definition.registryId)).toEqual([
      "eval_config:config-id",
      "eval_config:config-id",
    ]);
  });

  it("normalizes snake-case linked-span eval output types", () => {
    const [group] = buildFilterDefinitions([
      {
        id: "score-id",
        name: "Quality",
        group_by: "Evaluation Metrics",
        output_type: "score",
        property_kind: "eval_config",
      },
      {
        id: "pass-id",
        name: "Grounded",
        group_by: "Evaluation Metrics",
        output_type: "Pass/Fail",
        property_kind: "eval_config",
      },
      {
        id: "choice-id",
        name: "Tone",
        group_by: "Evaluation Metrics",
        output_type: "choices",
        choices: ["helpful", "unhelpful"],
        property_kind: "eval_config",
      },
    ]);

    expect(group.propertyName).toBe("Evaluation Metrics");
    expect(group.dependents.map((item) => item.filterType.type)).toEqual([
      "number",
      "boolean",
      "option",
    ]);
    expect(group.dependents[2]).toMatchObject({
      multiSelect: true,
      filterType: {
        options: [
          { label: "helpful", value: "helpful" },
          { label: "unhelpful", value: "unhelpful" },
        ],
      },
    });
  });

  it("uses numeric request filters for aggregate eval choices and trace counts", () => {
    const definitions = buildFilterDefinitions(
      [
        {
          id: "unique_traces",
          name: "No. of traces",
          property_kind: "system_attribute",
          filter_type: "number",
          supported_filter_ops: ["equals", "greater_than"],
        },
        {
          id: "config-id**good",
          name: "Good (Quality)",
          group_by: "Evaluation Metrics",
          output_type: "choices",
          property_kind: "eval_config",
          filter_type: "number",
          supported_filter_ops: ["equals", "between"],
        },
      ],
      true,
    );

    expect(definitions[0]).toMatchObject({
      propertyId: "unique_traces",
      filterType: { type: "number" },
      overrideOperators: [
        { label: "Equals", value: "equals" },
        { label: "Greater Than", value: "greater_than" },
      ],
    });
    expect(definitions[1].dependents[0].filterType.type).toBe("number");

    expect(
      normalizeFilters([
        {
          column_id: "unique_traces",
          filter_config: {
            filter_type: "number",
            filter_op: "equals",
            filter_value: ["12", ""],
          },
        },
        {
          column_id: "config-id**good",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: ["75", ""],
          },
        },
      ]),
    ).toEqual([
      {
        column_id: "unique_traces",
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: 12,
        },
      },
      {
        column_id: "config-id**good",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 75,
        },
      },
    ]);
  });

  it("limits UUID operators to the backend-published exact-match set", () => {
    const [definition] = buildFilterDefinitions([
      {
        id: "trace_id",
        name: "Trace Id",
        property_kind: "system_attribute",
        filter_type: "text",
        supported_filter_ops: ["equals", "not_equals"],
      },
    ]);

    expect(definition.showOperator).toBe(true);
    expect(
      definition.overrideOperators.map((operator) => operator.value),
    ).toEqual(["equals", "not_equals"]);
  });
});
