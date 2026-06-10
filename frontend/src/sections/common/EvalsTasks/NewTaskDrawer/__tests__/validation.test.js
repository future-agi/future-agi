import { describe, it, expect } from "vitest";
import { extractAttributeFilters, getNewTaskFilters } from "../validation";

const makeRow = (overrides = {}) => ({
  property: "attributes",
  propertyId: "ended_reason",
  apiColType: "SPAN_ATTRIBUTE",
  filterConfig: {
    filterType: "text",
    filterOp: "equals",
    filterValue: "completed",
  },
  ...overrides,
});

describe("extractAttributeFilters — colType round-trip", () => {
  it("emits the row's apiColType (SPAN_ATTRIBUTE) by default", () => {
    const out = extractAttributeFilters([makeRow()]);
    expect(out).toHaveLength(1);
    expect(out[0].filterConfig.colType).toBe("SPAN_ATTRIBUTE");
  });

  it("preserves apiColType=ANNOTATION (the bug behind TH-5645)", () => {
    const row = makeRow({
      propertyId: "annotator",
      apiColType: "ANNOTATION",
      filterConfig: {
        filterType: "text",
        filterOp: "equals",
        filterValue: "c65a0f3c-8a72-432a-987f-ddbd8391df29",
      },
    });
    const out = extractAttributeFilters([row]);
    expect(out[0].filterConfig.colType).toBe("ANNOTATION");
    expect(out[0].columnId).toBe("annotator");
  });

  it("preserves apiColType=SYSTEM_METRIC", () => {
    const row = makeRow({
      propertyId: "cost",
      apiColType: "SYSTEM_METRIC",
      filterConfig: {
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 0.5,
      },
    });
    const out = extractAttributeFilters([row]);
    expect(out[0].filterConfig.colType).toBe("SYSTEM_METRIC");
  });

  it("preserves apiColType=EVAL_METRIC", () => {
    const row = makeRow({
      propertyId: "4d808ee6-38bd-4cb2-9ed0-77d1c1488737",
      apiColType: "EVAL_METRIC",
      filterConfig: {
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 0.8,
      },
    });
    const out = extractAttributeFilters([row]);
    expect(out[0].filterConfig.colType).toBe("EVAL_METRIC");
  });

  it("falls back to SPAN_ATTRIBUTE when apiColType is missing", () => {
    const { apiColType, ...row } = makeRow();
    const out = extractAttributeFilters([row]);
    expect(out[0].filterConfig.colType).toBe("SPAN_ATTRIBUTE");
  });

  it("keeps each col_type when multiple rows are mixed", () => {
    const out = extractAttributeFilters([
      makeRow({ propertyId: "ended_reason" }),
      makeRow({
        propertyId: "annotator",
        apiColType: "ANNOTATION",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "uid-1",
        },
      }),
      makeRow({
        propertyId: "cost",
        apiColType: "SYSTEM_METRIC",
        filterConfig: {
          filterType: "number",
          filterOp: "greater_than",
          filterValue: 0.1,
        },
      }),
    ]);
    const byCol = Object.fromEntries(out.map((o) => [o.columnId, o.filterConfig.colType]));
    expect(byCol.ended_reason).toBe("SPAN_ATTRIBUTE");
    expect(byCol.annotator).toBe("ANNOTATION");
    expect(byCol.cost).toBe("SYSTEM_METRIC");
  });
});

describe("extractAttributeFilters — non-attribute rows go through the same list", () => {
  // The bug the FE PR fixes: an EVAL_METRIC chip used to land as a
  // top-level dict key (`b6a017ba-...: ["Pass"]`) which the BE dispatcher
  // silently dropped. Now it rides inside the canonical filters[] list
  // with col_type=EVAL_METRIC, matching list_spans_observe.
  it("emits an EVAL_METRIC chip as a filters[] item, not a top-level key", () => {
    const evalChip = {
      property: "b6a017ba-7683-4458-8a5d-d6aeaa37a2e8",
      propertyId: "b6a017ba-7683-4458-8a5d-d6aeaa37a2e8",
      fieldCategory: "eval",
      apiColType: "EVAL_METRIC",
      filterConfig: {
        filterType: "categorical",
        filterOp: "in",
        filterValue: ["Pass"],
      },
    };
    const out = extractAttributeFilters([evalChip]);
    expect(out).toHaveLength(1);
    expect(out[0].columnId).toBe(evalChip.propertyId);
    expect(out[0].filterConfig.colType).toBe("EVAL_METRIC");
    expect(out[0].filterConfig.filterValue).toEqual(["Pass"]);
  });

  it("emits a SYSTEM_METRIC chip (e.g. cost) as a filters[] item", () => {
    const sysChip = {
      property: "cost",
      propertyId: "cost",
      fieldCategory: "system",
      apiColType: "SYSTEM_METRIC",
      filterConfig: {
        filterType: "number",
        filterOp: "greater_than",
        filterValue: 0.5,
      },
    };
    const out = extractAttributeFilters([sysChip]);
    expect(out).toHaveLength(1);
    expect(out[0].columnId).toBe("cost");
    expect(out[0].filterConfig.colType).toBe("SYSTEM_METRIC");
  });

  it("emits an ANNOTATION annotator chip as a filters[] item", () => {
    const annChip = {
      property: "annotator",
      propertyId: "annotator",
      fieldCategory: "annotation",
      apiColType: "ANNOTATION",
      filterConfig: {
        filterType: "text",
        filterOp: "equals",
        filterValue: "c65a0f3c-8a72-432a-987f-ddbd8391df29",
      },
    };
    const out = extractAttributeFilters([annChip]);
    expect(out).toHaveLength(1);
    expect(out[0].columnId).toBe("annotator");
    expect(out[0].filterConfig.colType).toBe("ANNOTATION");
  });

  it("skips observation_type rows — they ride as a sibling top-level key", () => {
    const obsChip = {
      property: "observation_type",
      propertyId: "observation_type",
      filterConfig: { filterType: "text", filterOp: "in", filterValue: ["llm"] },
    };
    const out = extractAttributeFilters([obsChip]);
    expect(out).toHaveLength(0);
  });

  // node_type is the panel's FE alias for observation_type. It must NOT
  // ride through the canonical filters list (the BE SYSTEM_METRIC handler
  // can't resolve `node_type` against ObservationSpan without an
  // observation_type alias annotation that process_eval_task doesn't apply).
  it("skips node_type rows — they ride via the observation_type sibling key", () => {
    const nodeTypeChip = {
      property: "node_type",
      propertyId: "node_type",
      fieldCategory: "system",
      apiColType: "SYSTEM_METRIC",
      filterConfig: { filterType: "text", filterOp: "in", filterValue: ["llm"] },
    };
    const out = extractAttributeFilters([nodeTypeChip]);
    expect(out).toHaveLength(0);
  });

  // The TraceFilterPanel labels the global annotator chip with
  // apiColType="SYSTEM_METRIC" (a deliberate UI choice — see
  // TraceFilterPanel.jsx:372). If we let that through verbatim, the
  // eval-task BE dispatcher would feed the row to the SYSTEM_METRIC /
  // SPAN_ATTRIBUTE handlers too, where the column_id="annotator"
  // matches no rows → 0-span result poisons the combined AND.
  it("pins col_type=ANNOTATION for the annotator chip regardless of apiColType", () => {
    const row = {
      property: "annotator",
      propertyId: "annotator",
      fieldCategory: "annotation",
      apiColType: "SYSTEM_METRIC", // what TraceFilterPanel emits
      filterConfig: {
        filterType: "text",
        filterOp: "equals",
        filterValue: "d60a1556-8562-4a76-99e3-ab422a18e39e",
      },
    };
    const out = extractAttributeFilters([row]);
    expect(out).toHaveLength(1);
    expect(out[0].columnId).toBe("annotator");
    expect(out[0].filterConfig.colType).toBe("ANNOTATION");
  });

  it("pins col_type=ANNOTATION for my_annotations regardless of apiColType", () => {
    const row = {
      property: "my_annotations",
      propertyId: "my_annotations",
      apiColType: "SYSTEM_METRIC",
      filterConfig: { filterType: "boolean", filterOp: "equals", filterValue: true },
    };
    const out = extractAttributeFilters([row]);
    expect(out).toHaveLength(1);
    expect(out[0].filterConfig.colType).toBe("ANNOTATION");
  });

  // The static panel fields (status, model, service_name, …) at
  // TraceFilterPanel.jsx:1650-1666 don't always set apiColType, so the
  // form row arrives with apiColType=undefined. The fieldCategory
  // fallback ensures the wire still gets the right col_type.
  // (node_type is excluded — it's rerouted to the observation_type
  // sibling key; see the separate test below.)
  it("falls back to fieldCategory when apiColType is missing (status → SYSTEM_METRIC)", () => {
    const row = {
      property: "status",
      propertyId: "status",
      fieldCategory: "system",
      // apiColType deliberately omitted
      filterConfig: { filterType: "text", filterOp: "equals", filterValue: "OK" },
    };
    const out = extractAttributeFilters([row]);
    expect(out).toHaveLength(1);
    expect(out[0].columnId).toBe("status");
    expect(out[0].filterConfig.colType).toBe("SYSTEM_METRIC");
  });

  it("falls back to fieldCategory=annotation → ANNOTATION", () => {
    const row = {
      property: "some-label-uuid",
      propertyId: "some-label-uuid",
      fieldCategory: "annotation",
      filterConfig: {
        filterType: "categorical",
        filterOp: "equals",
        filterValue: "Pass",
      },
    };
    const out = extractAttributeFilters([row]);
    expect(out[0].filterConfig.colType).toBe("ANNOTATION");
  });

  it("getNewTaskFilters: a node_type chip lands under outer key 'observation_type'", () => {
    const data = {
      filters: [
        {
          property: "node_type",
          propertyId: "node_type",
          fieldCategory: "system",
          apiColType: "SYSTEM_METRIC",
          filterConfig: {
            filterType: "text",
            filterOp: "in",
            filterValue: ["llm"],
          },
        },
      ],
    };
    const { filters, attributeFilters } = getNewTaskFilters(
      data,
      "542cb448-ced4-420e-b728-bc55315e2e68",
      /* ignoreDate */ true,
    );
    expect(filters.observation_type).toEqual(["llm"]);
    expect(filters.node_type).toBeUndefined();
    expect(attributeFilters).toEqual([]);
  });

  it("skips legacy hydrated rows that carry no apiColType and no propertyId", () => {
    // Existing tasks may have top-level system keys hydrated as bare
    // form rows with property=<key> only. They were BE no-ops before;
    // we drop them here so re-save doesn't perpetuate the wrong shape.
    const legacyChip = {
      property: "some_legacy_key",
      filterConfig: { filterType: "text", filterOp: "equals", filterValue: "x" },
    };
    const out = extractAttributeFilters([legacyChip]);
    expect(out).toHaveLength(0);
  });
});
