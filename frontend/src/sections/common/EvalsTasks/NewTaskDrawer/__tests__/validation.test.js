import { describe, it, expect } from "vitest";
import { extractAttributeFilters } from "../validation";

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
