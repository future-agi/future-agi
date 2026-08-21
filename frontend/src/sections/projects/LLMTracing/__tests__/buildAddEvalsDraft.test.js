import { describe, expect, it } from "vitest";
import { serializeTaskFilterRowForApi } from "src/sections/common/EvalsTasks/task_filter_serialization";

import { toAddEvalsFormRows } from "../buildAddEvalsDraft";

describe("buildAddEvalsDraft property identity", () => {
  it("preserves eval registry identity and the canonical EVAL_METRIC type", () => {
    const [row] = toAddEvalsFormRows([
      {
        column_id: "quality",
        property_id: "eval_config:eval-config-1",
        filter_config: {
          col_type: "EVAL_METRIC",
          filter_type: "number",
          filter_op: "greater_than",
          filter_value: 0.8,
        },
      },
    ]);

    expect(row).toEqual(
      expect.objectContaining({
        property: "quality",
        propertyId: "quality",
        registryId: "eval_config:eval-config-1",
        fieldCategory: "eval",
        apiColType: "EVAL_METRIC",
        filterConfig: expect.objectContaining({
          colType: "EVAL_METRIC",
          filterType: "number",
          filterOp: "greater_than",
          filterValue: 0.8,
        }),
      }),
    );
    expect(serializeTaskFilterRowForApi(row)).toMatchObject({
      column_id: "quality",
      property_id: "eval_config:eval-config-1",
      filter_config: {
        col_type: "EVAL_METRIC",
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 0.8,
      },
    });
  });

  it("keeps same-name system and custom properties distinct in the draft", () => {
    const rows = toAddEvalsFormRows([
      {
        column_id: "model",
        property_id: "system_attribute:traces:model",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "gpt-4.1",
        },
      },
      {
        column_id: "model",
        property_id: "custom_attribute:model",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "equals",
          filter_value: "customer-model",
        },
      },
    ]);

    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          propertyId: "model",
          registryId: "system_attribute:traces:model",
          apiColType: "SYSTEM_METRIC",
        }),
        expect.objectContaining({
          propertyId: "model",
          registryId: "custom_attribute:model",
          apiColType: "SPAN_ATTRIBUTE",
        }),
      ]),
    );
  });
});
