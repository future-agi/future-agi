import { describe, expect, it } from "vitest";

import { getNewTaskFilters, NewTaskValidationSchema } from "../validation";
import { formatTaskFilters } from "../../common";

describe("eval task filter payload contract", () => {
  it.each(
    ["filters", "span_attributes_filters"].flatMap((wireKey) =>
      ["legacy", "raw"].map((source) => [wireKey, source]),
    ),
  )("round-trips %s %s session_id exclusions without changing source", (wireKey, source) => {
    const isRaw = source === "raw";
    const wire = {
      column_id: "session_id",
      ...(isRaw ? { property_id: "custom_attribute:session_id" } : {}),
      filter_config: {
        filter_type: "text",
        filter_op: "not_in",
        filter_value: isRaw ? ["001", 1, false] : ["session-a", "session-b"],
        ...(isRaw
          ? {
              col_type: "SPAN_ATTRIBUTE",
              attribute_value_types: ["string", "number", "boolean"],
            }
          : {}),
      },
    };
    const colType = isRaw ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC";
    const rows = formatTaskFilters({ [wireKey]: [wire] });
    expect(rows).toHaveLength(1);
    expect(rows[0].apiColType).toBe(colType);

    const saved = getNewTaskFilters({ filters: rows }, "project", true);
    expect(saved).toEqual({
      filters: { project_id: "project" },
      attributeFilters: [{
        ...wire,
        filter_config: { ...wire.filter_config, col_type: colType },
      }],
    });
    expect(getNewTaskFilters({
      filters: formatTaskFilters({ [wireKey]: saved.attributeFilters }),
    }, "project", true)).toEqual(saved);
  });

  it.each(["filters", "span_attributes_filters"])(
    "keeps source-less legacy annotation controls through %s hydration/save",
    (wireKey) => {
      for (const [column_id, filter_type, filter_value] of [
        ["annotator", "text", "00000000-0000-4000-8000-000000000123"],
        ["my_annotations", "boolean", false],
      ]) {
        const wire = {
          column_id,
          filter_config: { filter_type, filter_op: "equals", filter_value },
        };
        const rows = formatTaskFilters({ [wireKey]: [wire] });
        const { attributeFilters } = getNewTaskFilters(
          { runType: "continuous", filters: rows },
          "project",
          true,
        );
        expect(attributeFilters).toEqual([
          {
            ...wire,
            filter_config: { ...wire.filter_config, col_type: "ANNOTATION" },
          },
        ]);
      }
    },
  );

  it("keeps every explicit source when hydrating reserved annotation names", () => {
    for (const column_id of ["annotator", "my_annotations"]) {
      for (const col_type of [
        "SPAN_ATTRIBUTE",
        "ANNOTATION",
        "EVAL_METRIC",
        "SYSTEM_METRIC",
      ]) {
        const wire = {
          column_id,
          filter_config: {
            col_type,
            filter_type: "text",
            filter_op: "in",
            filter_value: ["001"],
          },
        };
        const rows = formatTaskFilters({ filters: [wire] });
        const { attributeFilters } = getNewTaskFilters(
          { runType: "continuous", filters: rows },
          "project",
          true,
        );
        expect(attributeFilters).toEqual([wire]);
      }
    }
  });

  it("keeps a hydrated legacy span restriction on save", () => {
    const rows = formatTaskFilters({ span_id: ["span-a", "span-b"] });
    expect(getNewTaskFilters({ filters: rows }, "project", true)).toEqual({
      filters: { project_id: "project", span_id: ["span-a", "span-b"] },
      attributeFilters: [],
    });
  });

  it.each(["SYSTEM_METRIC", "SPAN_ATTRIBUTE"])(
    "does not turn a canonical %s span_id exclusion into a positive sibling",
    (source) => {
      const row = {
        property: "span_id",
        propertyId: "span_id",
        apiColType: source,
        filterConfig: {
          filterType: "text",
          filterOp: "not_in",
          filterValue: ["span-a"],
        },
      };
      expect(getNewTaskFilters({ filters: [row] }, "project", true)).toEqual({
        filters: { project_id: "project" },
        attributeFilters: [{
          column_id: "span_id",
          filter_config: {
            col_type: source,
            filter_type: "text",
            filter_op: "not_in",
            filter_value: ["span-a"],
          },
        }],
      });
    },
  );

  it("hydrates property_id into registryId without replacing propertyId", () => {
    expect(
      formatTaskFilters({
        filters: [
          {
            column_id: "model",
            property_id: "custom_attribute:model",
            filter_config: {
              col_type: "SPAN_ATTRIBUTE",
              filter_type: "text",
              filter_op: "equals",
              filter_value: "tenant-model",
            },
          },
        ],
      }),
    ).toEqual([
      expect.objectContaining({
        property: "attributes",
        propertyId: "model",
        registryId: "custom_attribute:model",
      }),
    ]);
  });

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
            registryId: "custom_attribute:customer_tier",
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
        property_id: "custom_attribute:customer_tier",
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
          property_id: "custom_attribute:attempt",
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
        property_id: "custom_attribute:attempt",
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

  it("emits the canonical null filter_value for null-ops", () => {
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
    expect(attributeFilters[0].filter_config.filter_value).toBeNull();
  });
});

describe("spansLimit coercion", () => {
  // The custom row-limit input yields a string ("10"); the request contract
  // declares spans_limit as an integer, and strict request-contract
  // validation aborts the POST before it is sent — surfacing only a generic
  // "Something went wrong". Preset buttons set numbers and work.
  const baseForm = {
    name: "t",
    project: "1372e742-a10b-4d98-9ca4-31ef4d67115f",
    samplingRate: 50,
    evalsDetails: [{ id: "cfg-1" }],
    startDate: "2026-08-01",
    endDate: "2026-08-20",
    runType: "historical",
    rowType: "traces",
    filters: [],
  };

  it("coerces a custom string row limit to a number", async () => {
    const { NewTaskValidationSchema } = await import("../validation");
    const parsed = NewTaskValidationSchema().parse({
      ...baseForm,
      spansLimit: "10",
    });
    expect(parsed.spansLimit).toBe(10);
  });

  it("keeps preset numeric row limits as numbers", async () => {
    const { NewTaskValidationSchema } = await import("../validation");
    const parsed = NewTaskValidationSchema().parse({
      ...baseForm,
      spansLimit: 100000,
    });
    expect(parsed.spansLimit).toBe(100000);
  });
});
