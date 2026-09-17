import { describe, expect, it } from "vitest";

import { buildApiFilterArray } from "../TaskLivePreview";
import { formatTaskFilters } from "src/sections/common/EvalsTasks/common";
import { getNewTaskFilters } from "src/sections/common/EvalsTasks/NewTaskDrawer/validation";

const attrRow = (filterOp, filterValue) => ({
  property: "attributes",
  propertyId: "customer_tier",
  fieldCategory: "attribute",
  filterConfig: { filterType: "text", filterOp, filterValue },
});

describe("buildApiFilterArray — task live-preview wire builder", () => {
  it.each(
    ["filters", "span_attributes_filters"].flatMap((wireKey) =>
      ["trace_id", "span_id", "session"].map((key) => [wireKey, key]),
    ),
  )(
    "hydrates %s legacy %s as native without retyping explicit raw IDs",
    (wireKey, key) => {
      for (const raw of [false, true]) {
        for (const filter_op of ["in", "not_in"]) {
          const filter_config = {
            filter_type: "text",
            filter_op,
            filter_value: raw ? ["001", 1, false] : ["native-a", "native-b"],
            ...(raw && {
              col_type: "SPAN_ATTRIBUTE",
              attribute_value_types: ["string", "number", "boolean"],
            }),
          };
          const wire = {
            column_id: key,
            filter_config,
            ...(raw && { property_id: `custom_attribute:${key}` }),
          };
          const rows = formatTaskFilters({ [wireKey]: [wire] });
          expect(buildApiFilterArray(rows)).toEqual([wire]);
          expect(rows[0].apiColType).toBe(
            raw ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC",
          );
          const saved = getNewTaskFilters(
            { runType: "continuous", filters: rows },
            "project",
            true,
          );
          expect(saved.filters).toEqual({ project_id: "project" });
          expect(saved.attributeFilters).toEqual([
            {
              ...wire,
              filter_config: {
                ...filter_config,
                col_type: raw ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC",
              },
            },
          ]);
        }
      }
    },
  );

  it.each(["trace_id", "span_id", "session"])(
    "retains raw %s typed lists without changing legacy native ID rows",
    (key) => {
      for (const filterOp of ["in", "not_in"]) {
        const values = ["001", 1, false, "x".repeat(4097)];
        const types = ["string", "number", "boolean", "string"];
        expect(
          buildApiFilterArray([
            {
              ...attrRow(filterOp, values),
              propertyId: key,
              registryId: `custom_attribute:${key}`,
              apiColType: "SPAN_ATTRIBUTE",
              filterConfig: {
                filterType: "text",
                filterOp,
                filterValue: values,
                attributeValueTypes: types,
              },
            },
          ]),
        ).toEqual([
          {
            column_id: key,
            property_id: `custom_attribute:${key}`,
            filter_config: {
              filter_type: "text",
              filter_op: filterOp,
              filter_value: values,
              col_type: "SPAN_ATTRIBUTE",
              attribute_value_types: types,
            },
          },
        ]);
      }
      expect(
        buildApiFilterArray([
          {
            property: key,
            filterConfig: {
              filterType: "text",
              filterOp: "equals",
              filterValue: "native-id",
            },
          },
        ]),
      ).toEqual([
        {
          column_id: key,
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "native-id",
          },
        },
      ]);
    },
  );

  it.each(["ANNOTATION", "EVAL_METRIC"])(
    "does not strip explicit %s from an ID-named column",
    (apiColType) => {
      const [filter] = buildApiFilterArray([
        {
          ...attrRow("equals", "typed-id"),
          propertyId: "trace_id",
          apiColType,
        },
      ]);
      expect(filter.filter_config.col_type).toBe(apiColType);
    },
  );

  it.each(["ANNOTATION", "EVAL_METRIC", "SYSTEM_METRIC"])(
    "explicit %s wins over conflicting raw-attribute form hints",
    (colType) => {
      const row = {
        property: "attributes",
        propertyId: "annotator",
        apiColType: colType,
        fieldCategory: "attribute",
        filterConfig: {
          colType: "SPAN_ATTRIBUTE",
          filterType: "text",
          filterOp: "in",
          filterValue: ["reviewer"],
        },
      };
      const [filter] = buildApiFilterArray([row]);
      expect(filter.filter_config.col_type).toBe(colType);
      expect(filter.filter_config.filter_value).toEqual(["reviewer"]);
    },
  );

  it("nested source and category outrank the legacy attributes property", () => {
    for (const identity of [
      { filterConfig: { colType: "ANNOTATION" }, fieldCategory: "attribute" },
      { fieldCategory: "annotation" },
    ]) {
      const [filter] = buildApiFilterArray([
        {
          property: "attributes",
          propertyId: "my_annotations",
          ...identity,
          filterConfig: {
            ...identity.filterConfig,
            filterType: "boolean",
            filterOp: "equals",
            filterValue: false,
          },
        },
      ]);
      expect(filter.filter_config.col_type).toBe("ANNOTATION");
      expect(filter.filter_config.filter_value).toBe(false);
    }
  });

  it.each(
    ["annotator", "my_annotations"].flatMap((key) =>
      [
        ["apiColType", { apiColType: "SPAN_ATTRIBUTE" }],
        ["colType", { filterConfig: { colType: "SPAN_ATTRIBUTE" } }],
        ["category", { fieldCategory: "attribute" }],
        ["property", { property: "attributes" }],
      ].flatMap(([source, identity]) =>
        ["in", "not_in"].map((op) => [key, source, op, identity]),
      ),
    ),
  )("reserved raw %s honors %s for %s", (key, _source, op, identity) => {
    const values = ["001", 1, false, "x".repeat(4097)];
    const types = ["string", "number", "boolean", "string"];
    const row = {
      property: key,
      propertyId: key,
      registryId: `custom_attribute:${key}`,
      ...identity,
      filterConfig: {
        ...identity.filterConfig,
        filterType: "text",
        filterOp: op,
        filterValue: values,
        attributeValueTypes: types,
      },
    };
    const before = structuredClone(row);
    expect(buildApiFilterArray([row])).toEqual([
      {
        column_id: key,
        property_id: `custom_attribute:${key}`,
        filter_config: {
          filter_type: "text",
          filter_op: op,
          filter_value: values,
          col_type: "SPAN_ATTRIBUTE",
          attribute_value_types: types,
        },
      },
    ]);
    expect(row).toEqual(before);
  });

  it("reserved raw mixed two-filter clauses retain types and independent operators", () => {
    const rows = [
      ["annotator", "in", ["001", 1, false], ["string", "number", "boolean"]],
      [
        "my_annotations",
        "not_in",
        [true, "false", 0],
        ["boolean", "string", "number"],
      ],
    ].map(([key, op, values, types]) => ({
      property: "attributes",
      propertyId: key,
      apiColType: "SPAN_ATTRIBUTE",
      filterConfig: {
        filterType: "text",
        filterOp: op,
        filterValue: values,
        attributeValueTypes: types,
      },
    }));
    expect(buildApiFilterArray(rows)).toEqual(
      rows.map((row) => ({
        column_id: row.propertyId,
        filter_config: {
          filter_type: "text",
          filter_op: row.filterConfig.filterOp,
          filter_value: row.filterConfig.filterValue,
          col_type: "SPAN_ATTRIBUTE",
          attribute_value_types: row.filterConfig.attributeValueTypes,
        },
      })),
    );
  });

  it("reserved untyped legacy annotation controls keep their annotation family", () => {
    const annotators = ["00000000-0000-4000-8000-000000000123"];
    expect(
      buildApiFilterArray([
        {
          property: "annotator",
          filterConfig: {
            filterType: "text",
            filterOp: "in",
            filterValue: annotators,
          },
        },
        {
          property: "my_annotations",
          filterConfig: {
            filterType: "boolean",
            filterOp: "equals",
            filterValue: false,
          },
        },
      ]),
    ).toEqual([
      {
        column_id: "annotator",
        filter_config: {
          filter_type: "text",
          filter_op: "in",
          filter_value: annotators,
          col_type: "ANNOTATION",
        },
      },
      {
        column_id: "my_annotations",
        filter_config: {
          filter_type: "boolean",
          filter_op: "equals",
          filter_value: false,
          col_type: "ANNOTATION",
        },
      },
    ]);
  });

  it("preserves array contains alongside typed membership, cost, and historical dates", () => {
    const callIds = [
      "1000000001",
      "1000000002",
      "79",
      "call-4",
      "call-5",
      "call-6",
      "call-7",
    ];
    const filters = [
      { ...attrRow("in", ["10000001", "10000002"]), propertyId: "company_id" },
      {
        ...attrRow("in", ["unanswered_questions", "summary", "agent_4_6"]),
        propertyId: "prompt_slug",
      },
      {
        property: "total_cost",
        fieldCategory: "system",
        filterConfig: {
          filterType: "number",
          filterOp: "greater_than",
          filterValue: 0.01,
        },
      },
      {
        ...attrRow("contains", callIds),
        propertyId: "call_id",
        filterConfig: {
          filterType: "array",
          filterOp: "contains",
          filterValue: callIds,
        },
      },
    ];
    const original = JSON.stringify(filters);
    const out = buildApiFilterArray(
      filters,
      "2025-09-04T22:31:47Z",
      "2026-09-05T07:00:00Z",
    );

    expect(out).toHaveLength(5);
    expect(out[0].filter_config.filter_value).toEqual(["10000001", "10000002"]);
    expect(out[1].filter_config.filter_value).toEqual([
      "unanswered_questions",
      "summary",
      "agent_4_6",
    ]);
    expect(out[2].filter_config).toEqual({
      filter_type: "number",
      filter_op: "greater_than",
      filter_value: 0.01,
      col_type: "SYSTEM_METRIC",
    });
    expect(out[3].filter_config).toEqual({
      filter_type: "array",
      filter_op: "contains",
      filter_value: callIds,
      col_type: "SPAN_ATTRIBUTE",
    });
    expect(out[4].filter_config.filter_value).toEqual([
      "2025-09-04T22:31:47.000Z",
      "2026-09-05T07:00:00.000Z",
    ]);
    expect(JSON.stringify(filters)).toBe(original);
  });

  it("keeps property_id beside the native preview column", () => {
    const out = buildApiFilterArray([
      {
        ...attrRow("equals", "enterprise"),
        registryId: "custom_attribute:customer_tier",
      },
    ]);

    expect(out[0]).toMatchObject({
      column_id: "customer_tier",
      property_id: "custom_attribute:customer_tier",
    });
  });

  it("does not merge same-column rows — two not_contains stay two entries", () => {
    const out = buildApiFilterArray([
      attrRow("not_contains", "enterprise"),
      attrRow("not_contains", "startup"),
    ]);

    expect(out).toHaveLength(2);
    expect(out.every((f) => f.filter_config.filter_op === "not_contains")).toBe(
      true,
    );
    expect(out.map((f) => f.filter_config.filter_value)).toEqual([
      "enterprise",
      "startup",
    ]);
  });

  it("does not merge same-column string-equals (`in`) rows — two rows stay two entries (backend ANDs → matches nothing)", () => {
    const out = buildApiFilterArray([
      attrRow("in", "enterprise"),
      attrRow("in", "startup"),
    ]);

    expect(out).toHaveLength(2);
    expect(out.every((f) => f.filter_config.filter_op === "in")).toBe(true);
    expect(out.map((f) => f.filter_config.filter_value)).toEqual([
      ["enterprise"],
      ["startup"],
    ]);
  });

  // Known gap, pending a backend number `in` operator: two same-column number
  // `equals` rows can't be ORed (numbers have no `in`), so they stay two scalar
  // entries that the backend ANDs → matches nothing. This pins the current
  // contract, not desired behaviour.
  it("known gap (pending BE number-in): same-column number-equals rows stay two scalar entries — backend ANDs → matches nothing", () => {
    const numRow = (filterValue) => ({
      property: "attributes",
      propertyId: "token_count",
      fieldCategory: "attribute",
      filterConfig: { filterType: "number", filterOp: "equals", filterValue },
    });

    const out = buildApiFilterArray([numRow(5), numRow(7)]);

    expect(out).toHaveLength(2);
    expect(out.every((f) => f.filter_config.filter_op === "equals")).toBe(true);
    expect(out.map((f) => f.filter_config.filter_value)).toEqual([5, 7]);
  });

  it("coerces a scalar in value to a list so filter_value survives", () => {
    const out = buildApiFilterArray([attrRow("in", "enterprise")]);

    expect(out[0].filter_config.filter_op).toBe("in");
    expect(out[0].filter_config.filter_value).toEqual(["enterprise"]);
  });

  it("emits the canonical null filter_value for null-ops", () => {
    const out = buildApiFilterArray([attrRow("is_null", undefined)]);

    expect(out[0].filter_config.filter_op).toBe("is_null");
    expect(out[0].filter_config.filter_value).toBeNull();
  });

  it("keeps a range op as a two-element array (no scalar coercion)", () => {
    const out = buildApiFilterArray([
      {
        ...attrRow("between", [10, 20]),
        filterConfig: {
          filterType: "number",
          filterOp: "between",
          filterValue: [10, 20],
        },
      },
    ]);

    expect(out[0].filter_config.filter_op).toBe("between");
    expect(out[0].filter_config.filter_value).toEqual([10, 20]);
  });
});
