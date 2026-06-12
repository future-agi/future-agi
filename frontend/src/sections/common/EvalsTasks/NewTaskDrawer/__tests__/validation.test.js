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
