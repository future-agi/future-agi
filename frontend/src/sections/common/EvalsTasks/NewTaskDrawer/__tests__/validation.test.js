import { describe, expect, it } from "vitest";

import { getNewTaskFilters, NewTaskValidationSchema } from "../validation";

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

  it("preserves mixed text, array, and map filters in the task payload", () => {
    const { attributeFilters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "attributes",
            propertyId: "final_status",
            apiColType: "SPAN_ATTRIBUTE",
            filterConfig: {
              filterType: "text",
              filterOp: "in",
              filterValue: ["Rejected"],
            },
          },
          {
            property: "attributes",
            propertyId: "customer.tags",
            apiColType: "SPAN_ATTRIBUTE",
            filterConfig: {
              filterType: "array",
              filterOp: "contains",
              filterValue: ["vip", 3, true],
            },
          },
          {
            property: "attributes",
            propertyId: "customer.context",
            apiColType: "SPAN_ATTRIBUTE",
            filterConfig: {
              filterType: "map",
              filterOp: "contains",
              filterValue: { tier: "vip", attempt: 2 },
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(
      attributeFilters.map((row) => row.filter_config.filter_type),
    ).toEqual(["text", "array", "map"]);
    expect(attributeFilters[1].filter_config.filter_value).toEqual([
      "vip",
      3,
      true,
    ]);
    expect(attributeFilters[2].filter_config.filter_value).toEqual({
      tier: "vip",
      attempt: 2,
    });
  });

  it("preserves mixed scalar attribute types through the task form schema", () => {
    const result = NewTaskValidationSchema().parse({
      name: "Typed attribute task",
      project: "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      spansLimit: 100,
      samplingRate: 100,
      evalsDetails: [{ id: "eval-1" }],
      startDate: "2026-08-01T00:00:00.000Z",
      endDate: "2026-08-02T00:00:00.000Z",
      runType: "continuous",
      rowType: "traces",
      filters: [
        {
          property: "attributes",
          propertyId: "attempt",
          apiColType: "SPAN_ATTRIBUTE",
          filterConfig: {
            filterType: "text",
            filterOp: "in",
            filterValue: ["1", 1, true],
            attributeValueTypes: ["string", "number", "boolean"],
          },
        },
      ],
    });

    expect(result.filters.filters).toEqual([
      {
        column_id: "attempt",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: ["1", 1, true],
          col_type: "SPAN_ATTRIBUTE",
          attribute_value_types: ["string", "number", "boolean"],
        },
      },
    ]);
  });

  it("keeps direct source id filters for linked trace tasks", () => {
    const { filters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "trace_id",
            filterConfig: {
              filterType: "text",
              filterOp: "equals",
              filterValue: "trace-1",
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(filters).toEqual({
      project_id: "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      trace_id: ["trace-1"],
    });
  });

  it("does not merge same-column rows — two not_contains stay two entries", () => {
    const { attributeFilters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "attributes",
            propertyId: "customer_tier",
            filterConfig: {
              filterType: "text",
              filterOp: "not_contains",
              filterValue: "enterprise",
            },
          },
          {
            property: "attributes",
            propertyId: "customer_tier",
            filterConfig: {
              filterType: "text",
              filterOp: "not_contains",
              filterValue: "startup",
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters).toHaveLength(2);
    expect(
      attributeFilters.every(
        (f) => f.filter_config.filter_op === "not_contains",
      ),
    ).toBe(true);
    expect(attributeFilters.map((f) => f.filter_config.filter_value)).toEqual([
      "enterprise",
      "startup",
    ]);
  });

  it("coerces a scalar in value to a list so filter_value survives", () => {
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
              filterValue: "enterprise",
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters[0].filter_config.filter_op).toBe("in");
    expect(attributeFilters[0].filter_config.filter_value).toEqual([
      "enterprise",
    ]);
  });

  it("does not merge same-column string-equals (`in`) rows — two rows stay two entries (backend ANDs → matches nothing)", () => {
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
              filterValue: "enterprise",
            },
          },
          {
            property: "attributes",
            propertyId: "customer_tier",
            filterConfig: {
              filterType: "text",
              filterOp: "in",
              filterValue: "startup",
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters).toHaveLength(2);
    expect(
      attributeFilters.every((f) => f.filter_config.filter_op === "in"),
    ).toBe(true);
    expect(attributeFilters.map((f) => f.filter_config.filter_value)).toEqual([
      ["enterprise"],
      ["startup"],
    ]);
  });

  it("does not merge same-column number-equals rows — two `equals` rows stay two scalar entries (backend ANDs → matches nothing)", () => {
    const { attributeFilters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "attributes",
            propertyId: "token_count",
            filterConfig: {
              filterType: "number",
              filterOp: "equals",
              filterValue: 5,
            },
          },
          {
            property: "attributes",
            propertyId: "token_count",
            filterConfig: {
              filterType: "number",
              filterOp: "equals",
              filterValue: 7,
            },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters).toHaveLength(2);
    expect(
      attributeFilters.every((f) => f.filter_config.filter_op === "equals"),
    ).toBe(true);
    expect(attributeFilters.map((f) => f.filter_config.filter_value)).toEqual([
      5, 7,
    ]);
  });

  it("emits an empty-string filter_value for null-ops", () => {
    const { attributeFilters } = getNewTaskFilters(
      {
        runType: "continuous",
        filters: [
          {
            property: "attributes",
            propertyId: "customer_tier",
            filterConfig: { filterType: "text", filterOp: "is_null" },
          },
        ],
      },
      "1372e742-a10b-4d98-9ca4-31ef4d67115f",
      true,
    );

    expect(attributeFilters[0].filter_config.filter_op).toBe("is_null");
    expect(attributeFilters[0].filter_config.filter_value).toBe("");
  });
});
