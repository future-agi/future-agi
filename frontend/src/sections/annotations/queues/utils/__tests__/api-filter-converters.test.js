import { describe, expect, it } from "vitest";

import { apiFilterToPanel, panelFilterToApi } from "../api-filter-converters";

describe("annotation queue API filter converters", () => {
  it("builds canonical API filters from panel rows", () => {
    const apiFilter = panelFilterToApi({
      field: "quality_eval",
      fieldName: "Quality Eval",
      fieldCategory: "eval",
      fieldType: "number",
      operator: "greater_than",
      value: "80",
    });

    expect(apiFilter).toEqual({
      column_id: "quality_eval",
      display_name: "Quality Eval",
      filter_config: {
        filter_type: "number",
        filter_op: "greater_than",
        filter_value: 80,
        col_type: "EVAL_METRIC",
      },
    });
    expect(apiFilter).not.toHaveProperty("columnId");
    expect(apiFilter).not.toHaveProperty("filterConfig");
  });

  it("can include local metadata without changing the API filter body", () => {
    const apiFilter = panelFilterToApi(
      {
        field: "customer_tier",
        fieldName: "Customer Tier",
        fieldCategory: "attribute",
        fieldType: "string",
        operator: "in",
        value: ["vip"],
      },
      { includeMeta: true },
    );

    expect(apiFilter).toMatchObject({
      column_id: "customer_tier",
      filter_config: {
        filter_type: "text",
        filter_op: "in",
        filter_value: ["vip"],
        col_type: "SPAN_ATTRIBUTE",
      },
      _meta: { parentProperty: "" },
    });
  });

  it("rejects legacy panel-only operators before the API call", () => {
    expect(() =>
      panelFilterToApi({
        field: "customer_tier",
        fieldCategory: "attribute",
        fieldType: "string",
        operator: "is",
        value: ["vip"],
      }),
    ).toThrow(/Unsupported filter operator/);
  });

  it("hydrates canonical API filters back to panel rows", () => {
    const panelFilter = apiFilterToPanel(
      {
        column_id: "created_at",
        display_name: "Created At",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-01-01T00:00:00.000Z",
            "2026-01-02T00:00:00.000Z",
          ],
          col_type: "SYSTEM_METRIC",
        },
      },
      { dateFieldType: "date" },
    );

    expect(panelFilter).toEqual({
      field: "created_at",
      fieldName: "Created At",
      fieldCategory: "system",
      fieldType: "date",
      operator: "between",
      value: ["2026-01-01T00:00", "2026-01-02T00:00"],
    });
  });

  it("hydrates boolean filters into the scalar value shape the panel select expects", () => {
    const panelFilter = apiFilterToPanel({
      column_id: "persona.multilingual",
      display_name: "Multilingual",
      filter_config: {
        filter_type: "boolean",
        filter_op: "equals",
        filter_value: false,
        col_type: "SYSTEM_METRIC",
      },
    });

    expect(panelFilter).toMatchObject({
      field: "persona.multilingual",
      fieldType: "boolean",
      operator: "equals",
      value: "false",
    });
  });
});
