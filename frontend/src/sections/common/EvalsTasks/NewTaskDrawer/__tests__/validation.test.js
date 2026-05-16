import { describe, expect, it } from "vitest";

import { getNewTaskFilters } from "../validation";

describe("eval task filter payload contract", () => {
  it("maps task panel span kind to the backend observation_type key", () => {
    const { filters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "span_kind",
            filterConfig: {
              filterType: "text",
              filterOp: "in",
              filterValue: ["llm", "tool"],
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(filters).toEqual({
      project_id: "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      observation_type: ["llm", "tool"],
    });
    expect(filters).not.toHaveProperty("span_kind");
  });

  it("serializes span attributes as canonical snake_case filter objects", () => {
    const { attributeFilters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "attributes",
            propertyId: "customer_tier",
            filterConfig: {
              filterType: "text",
              filterOp: "in",
              filterValue: ["enterprise", "startup"],
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters).toEqual([
      {
        column_id: "customer_tier",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "in",
          filter_value: ["enterprise", "startup"],
        },
      },
    ]);
    expect(attributeFilters[0]).not.toHaveProperty("columnId");
    expect(attributeFilters[0]).not.toHaveProperty("filterConfig");
  });
});
