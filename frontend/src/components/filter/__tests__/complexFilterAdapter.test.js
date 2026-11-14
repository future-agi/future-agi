import { describe, expect, it } from "vitest";
import {
  buildFilterLeaves,
  fromPanelValue,
  leavesToFields,
  toPanelValue,
} from "../complexFilterAdapter";
import { getComplexFilterValidation } from "src/components/ComplexFilter/common";
import { fieldsToProperties } from "../fieldSchema";

const definition = [
  {
    propertyName: "Name",
    propertyId: "name",
    filterType: { type: "text" },
    defaultFilter: "contains",
  },
  { propertyName: "Rate", propertyId: "rate", filterType: { type: "number" } },
  {
    propertyName: "Created",
    propertyId: "created_at",
    filterType: { type: "date" },
  },
  {
    propertyName: "Status",
    propertyId: "status",
    filterType: {
      type: "option",
      options: [
        { value: "running", label: "Running" },
        { value: "failed", label: "Failed" },
      ],
    },
  },
  {
    propertyName: "Node Type",
    propertyId: "node_type",
    multiSelect: true,
    filterType: {
      type: "option",
      options: [
        { value: "llm", label: "LLM" },
        { value: "tool", label: "Tool" },
      ],
    },
  },
  {
    propertyName: "Pinned",
    propertyId: "pinned",
    filterType: { type: "boolean", truthLabel: "Yes", falseLabel: "No" },
  },
  {
    propertyName: "Evaluation Metrics",
    stringConnector: "is",
    dependents: [
      {
        propertyName: "Toxicity",
        propertyId: "eval-1",
        filterType: {
          type: "number",
          options: [
            { label: "Passed", value: 0 },
            { label: "Failed", value: 100 },
          ],
        },
      },
      {
        propertyName: "Score",
        propertyId: "eval-2",
        filterType: { type: "number" },
      },
    ],
  },
  {
    propertyName: "Annotation Metrics",
    stringConnector: "by",
    dependents: [
      {
        propertyName: "Label name",
        propertyId: "label_name",
        dependents: [
          {
            propertyName: "Sentiment",
            propertyId: "label-1",
            filterType: { type: "text" },
            dependents: [
              {
                propertyName: "Positive",
                propertyId: "label-1**positive",
                filterType: { type: "number" },
                defaultFilter: "greater_than",
                defaultFilterValue: 0,
                hideValueSelector: true,
              },
              {
                propertyName: "Negative",
                propertyId: "label-1**negative",
                filterType: { type: "number" },
                defaultFilter: "greater_than",
                defaultFilterValue: 0,
                hideValueSelector: true,
              },
            ],
          },
        ],
      },
    ],
  },
];

const strip = (filters) => filters.map(({ id, ...rest }) => rest);

describe("complexFilterAdapter", () => {
  const leaves = buildFilterLeaves(definition, []);

  it("flattens the tree into one field per selectable property", () => {
    const fields = leavesToFields(leaves);
    expect(fields.map((f) => f.id)).toEqual([
      "name",
      "rate",
      "created_at",
      "status",
      "node_type",
      "pinned",
      "eval-1",
      "eval-2",
      "label-1",
    ]);
    const byId = Object.fromEntries(fields.map((f) => [f.id, f]));
    expect(byId.name).toMatchObject({ type: "text", operators: ["contains"] });
    expect(byId.status).toMatchObject({ type: "enum", operators: ["equals"] });
    expect(byId.node_type).toMatchObject({
      type: "array",
      operators: ["contains"],
    });
    expect(byId["eval-1"].category).toBe("Evaluation Metrics");
    expect(byId["label-1"].options.map((o) => o.label)).toEqual([
      "Positive",
      "Negative",
    ]);
  });

  it("writes the same rows the value selectors wrote", () => {
    const rows = [
      {
        field: "name",
        fieldType: "string",
        operator: "contains",
        value: ["abc"],
      },
      {
        field: "rate",
        fieldType: "number",
        operator: "between",
        value: ["1", "5"],
      },
      {
        field: "rate",
        fieldType: "number",
        operator: "greater_than",
        value: "2",
      },
      {
        field: "status",
        fieldType: "categorical",
        operator: "equals",
        value: ["failed"],
      },
      {
        field: "node_type",
        fieldType: "array",
        operator: "contains",
        value: ["llm", "tool"],
      },
      {
        field: "pinned",
        fieldType: "boolean",
        operator: "equals",
        value: "true",
      },
      {
        field: "eval-1",
        fieldType: "categorical",
        operator: "equals",
        value: [100],
      },
      {
        field: "eval-2",
        fieldType: "number",
        operator: "less_than",
        value: "7",
      },
      {
        field: "label-1",
        fieldType: "categorical",
        operator: "equals",
        value: ["label-1**negative"],
      },
    ];
    const filters = fromPanelValue(rows, leaves);
    expect(strip(filters)).toEqual([
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "abc",
        },
        _meta: { parentProperty: "name" },
      },
      {
        column_id: "rate",
        filter_config: {
          filter_type: "number",
          filter_op: "between",
          filter_value: ["1", "5"],
        },
        _meta: { parentProperty: "rate" },
      },
      {
        column_id: "rate",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: ["2", ""],
        },
        _meta: { parentProperty: "rate" },
      },
      {
        column_id: "status",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "failed",
        },
        _meta: { parentProperty: "status" },
      },
      {
        column_id: "node_type",
        filter_config: {
          filter_type: "array",
          filter_op: "contains",
          filter_value: ["llm", "tool"],
        },
        _meta: { parentProperty: "node_type" },
      },
      {
        column_id: "pinned",
        filter_config: {
          filter_type: "boolean",
          filter_op: "equals",
          filter_value: true,
        },
        _meta: { parentProperty: "pinned" },
      },
      {
        column_id: "eval-1",
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: [100, ""],
        },
        _meta: {
          parentProperty: "Evaluation Metrics",
          "Evaluation Metrics": "eval-1",
        },
      },
      {
        column_id: "eval-2",
        filter_config: {
          filter_type: "number",
          filter_op: "less_than",
          filter_value: ["7", ""],
        },
        _meta: {
          parentProperty: "Evaluation Metrics",
          "Evaluation Metrics": "eval-2",
        },
      },
      {
        column_id: "label-1**negative",
        filter_config: {
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 0,
        },
        _meta: {
          parentProperty: "Annotation Metrics",
          "Annotation Metrics": "label_name",
          label_name: "label-1",
          "label-1": "label-1**negative",
        },
      },
    ]);
    // every row the panel writes passes the same validation the grids use
    const validation = getComplexFilterValidation(true);
    filters.forEach((filter) => {
      expect(validation.safeParse(filter).success).toBe(true);
    });
  });

  it("sends one string for plain text, as the old filter did", () => {
    const text = buildFilterLeaves(
      [
        {
          propertyName: "Name",
          propertyId: "name",
          filterType: { type: "text" },
        },
      ],
      [],
    );
    const [one] = fromPanelValue(
      [{ field: "name", operator: "in", value: ["a"] }],
      text,
    );
    expect(one.filter_config).toMatchObject({
      filter_op: "equals",
      filter_value: "a",
    });
    const [many] = fromPanelValue(
      [{ field: "name", operator: "in", value: ["a", "b"] }],
      text,
    );
    // The old text filter sent one string, so extra values never go out.
    expect(many.filter_config).toMatchObject({
      filter_op: "equals",
      filter_value: "a",
    });
    expect(leavesToFields(text)[0]).toMatchObject({
      operators: ["in"],
      multiple: false,
    });
  });

  it("round-trips stored state through the panel", () => {
    const stored = fromPanelValue(
      [
        { field: "name", operator: "contains", value: ["abc"] },
        { field: "rate", operator: "between", value: ["1", "5"] },
        {
          field: "created_at",
          operator: "between",
          value: ["2026-01-02T00:00", "2026-01-09T10:30"],
        },
        { field: "status", operator: "equals", value: ["failed"] },
        { field: "eval-1", operator: "equals", value: [0] },
        { field: "label-1", operator: "equals", value: ["label-1**positive"] },
      ],
      leaves,
    );
    const rows = toPanelValue(stored, leaves);
    expect(
      rows.map(({ field, operator, value }) => ({ field, operator, value })),
    ).toEqual([
      { field: "name", operator: "contains", value: ["abc"] },
      { field: "rate", operator: "between", value: ["1", "5"] },
      {
        field: "created_at",
        operator: "between",
        value: ["2026-01-02T00:00", "2026-01-09T10:30"],
      },
      { field: "status", operator: "equals", value: ["failed"] },
      { field: "eval-1", operator: "equals", value: [0] },
      { field: "label-1", operator: "equals", value: ["label-1**positive"] },
    ]);
  });

  it("reads rows written by quick filters and ignores the blank default row", () => {
    const quick = [
      {
        id: "a",
        column_id: "",
        filter_config: { filter_type: "", filter_op: "", filter_value: "" },
      },
      {
        id: "b",
        column_id: "node_type",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: ["llm"],
        },
        _meta: { parentProperty: "node_type" },
      },
      {
        id: "c",
        column_id: "eval-2",
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: [3, ""],
        },
        _meta: {
          parentProperty: "Evaluation Metrics",
          "Evaluation Metrics": "eval-2",
        },
      },
    ];
    const rows = toPanelValue(quick, leaves);
    expect(
      rows.map(({ field, operator, value }) => ({ field, operator, value })),
    ).toEqual([
      { field: "node_type", operator: "contains", value: ["llm"] },
      { field: "eval-2", operator: "equals", value: "3" },
    ]);
  });

  it("keeps rows the definition no longer lists visible and editable", () => {
    const orphan = {
      id: "z",
      column_id: "gone",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "x",
        col_type: "SPAN_ATTRIBUTE",
      },
      _meta: { parentProperty: "Attribute", Attribute: "gone" },
    };
    const withOrphan = buildFilterLeaves(definition, [orphan]);
    const rows = toPanelValue([orphan], withOrphan);
    expect(rows).toHaveLength(1);
    const [back] = fromPanelValue(rows, withOrphan);
    expect(back.column_id).toBe("gone");
    expect(back._meta).toEqual(orphan._meta);
    expect(back.filter_config.col_type).toBe("SPAN_ATTRIBUTE");
  });

  it("falls back to the blank default row when everything is cleared", () => {
    const defaultFilter = {
      column_id: "",
      filter_config: { filter_type: "", filter_op: "", filter_value: "" },
    };
    const next = fromPanelValue([], leaves, { defaultFilter });
    expect(next).toHaveLength(1);
    expect(next[0].column_id).toBe("");
    expect(next[0].id).toBeTruthy();
  });

  describe("span attributes", () => {
    const attributeDefinition = [
      {
        propertyName: "Attribute",
        stringConnector: "is",
        dependents: [
          {
            propertyName: "llm.model",
            propertyId: "llm.model",
            registryId: "custom_attribute:llm.model",
            maxUsage: 1,
            allowTypeChange: true,
            showOperator: true,
            asyncOptions: true,
            filterType: { type: "text" },
            attributeTypes: ["string"],
          },
          {
            propertyName: "custom.x",
            propertyId: "custom.x",
            registryId: "custom_attribute:custom.x",
            maxUsage: 1,
            allowTypeChange: true,
            showOperator: true,
            filterType: { type: "text" },
          },
        ],
      },
    ];
    const attributeLeaves = buildFilterLeaves(attributeDefinition, []);

    it("offers attributes through the panel's attribute picker", () => {
      const [typed, untyped] = leavesToFields(attributeLeaves);
      expect(typed).toMatchObject({
        id: "llm.model",
        category: "attribute",
        apiColType: "SPAN_ATTRIBUTE",
        registryId: "custom_attribute:llm.model",
        attributeTypes: ["string"],
      });
      expect(typed.typeSelectable).toBeUndefined();
      expect(typed.operators).toBeUndefined();
      // Unknown storage type: the user picks Text / Number / Boolean.
      expect(untyped).toMatchObject({ id: "custom.x", typeSelectable: true });
      expect(fieldsToProperties([untyped])[0].type).toBe("text");
    });

    it("keeps typed values on list operators", () => {
      const [filter] = fromPanelValue(
        [
          {
            field: "llm.model",
            fieldType: "string",
            fieldCategory: "attribute",
            operator: "in",
            value: ["gpt-4", "gpt-3"],
            valueTypes: ["string", "string"],
          },
        ],
        attributeLeaves,
      );
      expect(filter.filter_config).toEqual({
        filter_type: "text",
        filter_op: "in",
        filter_value: ["gpt-4", "gpt-3"],
        attribute_value_types: ["string", "string"],
        col_type: "SPAN_ATTRIBUTE",
      });
      expect(filter.column_id).toBe("llm.model");
      expect(filter.registryId).toBe("custom_attribute:llm.model");
      expect(filter._meta.parentProperty).toBe("Attribute");
      expect(getComplexFilterValidation(true).safeParse(filter).success).toBe(
        true,
      );
      const [row] = toPanelValue([filter], attributeLeaves);
      expect(row).toMatchObject({
        field: "llm.model",
        operator: "in",
        value: ["gpt-4", "gpt-3"],
        valueTypes: ["string", "string"],
      });
    });

    it("writes a number attribute the way the type switch did", () => {
      const [filter] = fromPanelValue(
        [
          {
            field: "custom.x",
            fieldType: "number",
            fieldCategory: "attribute",
            operator: "greater_than",
            value: "3",
          },
        ],
        attributeLeaves,
      );
      expect(filter.filter_config).toMatchObject({
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: ["3", ""],
        col_type: "SPAN_ATTRIBUTE",
      });
    });

    it("accepts an attribute the panel found by exact name", () => {
      const [filter] = fromPanelValue(
        [
          {
            field: "not.loaded",
            registryId: "custom_attribute:not.loaded",
            fieldType: "string",
            fieldCategory: "attribute",
            operator: "contains",
            value: ["x"],
          },
        ],
        attributeLeaves,
      );
      expect(filter.column_id).toBe("not.loaded");
      expect(filter.filter_config.col_type).toBe("SPAN_ATTRIBUTE");
      expect(filter._meta.parentProperty).toBe("Attribute");
    });
  });
});
