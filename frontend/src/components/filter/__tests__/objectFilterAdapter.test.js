import { describe, expect, it } from "vitest";
import {
  fromPanelValue,
  objectFieldsToFields,
  toPanelValue,
} from "../objectFilterAdapter";

const filterFields = [
  {
    value: "type",
    label: "Category",
    type: "enum",
    choices: ["prebuilt", "custom"],
    operators: ["is"],
    single: true,
  },
  {
    value: "simulation_type",
    label: "Agent Type",
    type: "enum",
    choices: ["voice", "text"],
    choiceLabels: { voice: "Voice", text: "Chat" },
    single: true,
  },
  {
    value: "eval_type",
    label: "Eval Type",
    type: "enum",
    choices: ["llm", "code"],
  },
];

describe("objectFilterAdapter", () => {
  it("describes enum fields as single or multi pickers with human labels", () => {
    const [type, sim, evalType] = objectFieldsToFields(filterFields);
    expect(type).toMatchObject({
      id: "type",
      type: "enum",
      multiple: false,
      operators: ["equals"],
    });
    expect(sim.options).toEqual([
      { value: "voice", label: "Voice" },
      { value: "text", label: "Chat" },
    ]);
    expect(sim.operators).toEqual(["equals", "not_equals"]);
    expect(evalType).toMatchObject({
      multiple: true,
      operators: ["in", "not_in"],
    });
  });

  it("reads object filters as one row per included or excluded set", () => {
    const rows = toPanelValue(
      {
        eval_type: ["llm", "code"],
        eval_type_not: ["agent"],
        type: ["custom"],
      },
      filterFields,
    );
    expect(
      rows.map(({ field, operator, value }) => ({ field, operator, value })),
    ).toEqual([
      { field: "eval_type", operator: "in", value: ["llm", "code"] },
      { field: "eval_type", operator: "not_in", value: ["agent"] },
      { field: "type", operator: "equals", value: ["custom"] },
    ]);
  });

  it("writes the same object the old panel did", () => {
    expect(
      fromPanelValue(
        [
          { field: "eval_type", operator: "in", value: ["llm"] },
          { field: "eval_type", operator: "in", value: ["llm", "code"] },
          { field: "eval_type", operator: "not_in", value: ["agent"] },
          { field: "type", operator: "equals", value: ["custom"] },
          {
            field: "simulation_type",
            operator: "not_equals",
            value: ["voice"],
          },
        ],
        filterFields,
      ),
    ).toEqual({
      eval_type: ["llm", "code"],
      eval_type_not: ["agent"],
      type: ["custom"],
      simulation_type_not: ["voice"],
    });
  });

  it("keeps a single-value field to one value and nothing becomes null", () => {
    expect(
      fromPanelValue(
        [{ field: "type", operator: "equals", value: ["custom", "prebuilt"] }],
        filterFields,
      ),
    ).toEqual({ type: ["custom"] });
    expect(fromPanelValue([], filterFields)).toBeNull();
    expect(
      fromPanelValue(
        [{ field: "type", operator: "equals", value: [] }],
        filterFields,
      ),
    ).toBeNull();
  });

  it("passes through keys the fields do not describe", () => {
    expect(
      fromPanelValue(
        [{ field: "type", operator: "equals", value: ["custom"] }],
        filterFields,
        { search: "abc", type: ["prebuilt"] },
      ),
    ).toEqual({ search: "abc", type: ["custom"] });
  });
});
