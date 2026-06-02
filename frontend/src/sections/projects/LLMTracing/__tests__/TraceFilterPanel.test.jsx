import { describe, it, expect } from "vitest";
import {
  buildTraceFilterProperties,
  filterPropertiesForPicker,
  getTraceFilterFields,
  normalizeFilterRowOperator,
  shouldUseSingleSelectValuePicker,
} from "../TraceFilterPanel";
import {
  getPickerOptionSearchText,
  getPickerOptionSecondaryLabel,
} from "../filterValuePickerUtils";

describe("getTraceFilterFields (TH-4571)", () => {
  it("prepends Trace ID when tab is 'trace'", () => {
    const fields = getTraceFilterFields("trace");
    expect(fields[0]).toMatchObject({
      value: "trace_id",
      label: "Trace ID",
      type: "string",
    });
    expect(fields.some((f) => f.value === "span_id")).toBe(false);
  });

  it("prepends Trace ID and Span ID when tab is 'spans'", () => {
    const fields = getTraceFilterFields("spans");
    expect(fields[0]).toMatchObject({ value: "trace_id", label: "Trace ID" });
    expect(fields[1]).toMatchObject({ value: "span_id", label: "Span ID" });
  });

  it("returns base fields unchanged when tab is null/undefined/unknown", () => {
    const fromNull = getTraceFilterFields(null);
    const fromUndefined = getTraceFilterFields(undefined);
    const fromUnknown = getTraceFilterFields("bogus");

    // None of the fallback calls should inject trace_id or span_id
    [fromNull, fromUndefined, fromUnknown].forEach((fields) => {
      expect(fields.some((f) => f.value === "trace_id")).toBe(false);
      expect(fields.some((f) => f.value === "span_id")).toBe(false);
    });

    // All fallbacks must return the same base list (same reference semantics
    // are not required; structural equality is what consumers rely on).
    expect(fromNull).toEqual(fromUndefined);
    expect(fromNull).toEqual(fromUnknown);
  });
});

describe("normalizeFilterRowOperator", () => {
  it("maps list operators to canonical equality panel operators before apply", () => {
    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "in",
        value: ["OK"],
      }).operator,
    ).toBe("equals");

    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "not_in",
        value: ["ERROR"],
      }).operator,
    ).toBe("not_equals");
  });

  it("keeps canonical number and date ops", () => {
    expect(
      normalizeFilterRowOperator({
        field: "latency_ms",
        fieldType: "number",
        operator: "equals",
        value: "100",
      }).operator,
    ).toBe("equals");

    expect(
      normalizeFilterRowOperator({
        field: "created_at",
        fieldType: "date",
        operator: "less_than",
        value: "2026-05-09T00:00",
      }).operator,
    ).toBe("less_than");
  });

  it("falls back to exact multi-select operators for restricted id fields", () => {
    expect(
      normalizeFilterRowOperator({
        field: "trace_id",
        fieldType: "string",
        operator: "contains",
        value: "abc",
      }).operator,
    ).toBe("in");

    expect(
      normalizeFilterRowOperator({
        field: "span_id",
        fieldType: "string",
        operator: "contains",
        value: "abc",
      }).operator,
    ).toBe("in");
  });

  it("keeps canonical annotation equality operators for the restricted annotator operator", () => {
    expect(
      normalizeFilterRowOperator({
        field: "annotator",
        fieldType: "annotator",
        operator: "equals",
        value: ["user-a", "user-b"],
      }).operator,
    ).toBe("equals");
  });

  it("preserves no-value operators for eval and annotation filter rows", () => {
    for (const fieldType of ["categorical", "thumbs", "annotator", "date"]) {
      expect(
        normalizeFilterRowOperator({
          field: `${fieldType}-field`,
          fieldType,
          operator: "is_null",
          value: "",
        }).operator,
      ).toBe("is_null");
    }
  });

  it("keeps annotator, categorical, and direct ID filters multi-selectable", () => {
    expect(
      shouldUseSingleSelectValuePicker(
        { field: "annotator", fieldType: "annotator" },
        "equals",
      ),
    ).toBe(false);
    expect(
      shouldUseSingleSelectValuePicker(
        { field: "quality", fieldType: "categorical" },
        "equals",
      ),
    ).toBe(false);
    expect(
      shouldUseSingleSelectValuePicker(
        { field: "trace_id", fieldType: "string" },
        "in",
      ),
    ).toBe(false);
    expect(
      shouldUseSingleSelectValuePicker(
        { field: "span_id", fieldType: "string" },
        "not_in",
      ),
    ).toBe(false);
    expect(
      shouldUseSingleSelectValuePicker(
        { field: "trace_name", fieldType: "string" },
        "contains",
      ),
    ).toBe(true);
  });
});

describe("annotator annotation filter (TH-4710)", () => {
  it("does not show ended_reason for unrelated property search text (TH-5149)", () => {
    const properties = [
      {
        id: "ended_reason",
        name: "Ended Reason",
        category: "attribute",
        type: "string",
      },
      {
        id: "status",
        name: "Status",
        category: "system",
        type: "string",
      },
    ];

    expect(
      filterPropertiesForPicker({
        properties,
        search: "xqz-not-a-match",
      }),
    ).toEqual([]);
    expect(
      filterPropertiesForPicker({
        properties,
        search: "ended reason",
      }),
    ).toEqual([properties[0]]);
  });

  it("only exposes span-owned metrics when building span filter properties", () => {
    const metrics = [
      {
        name: "latency",
        display_name: "Latency",
        category: "system_metric",
        source: "traces",
        type: "number",
      },
      {
        name: "latency_ms",
        display_name: "Duration",
        category: "system_metric",
        source: "spans",
        sources: ["spans"],
        type: "number",
      },
    ];

    expect(
      buildTraceFilterProperties(metrics, { sourceScope: "traces" }).some(
        (property) => property.id === "latency_ms",
      ),
    ).toBe(false);

    expect(
      buildTraceFilterProperties(metrics, { sourceScope: "spans" }),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "latency_ms",
          name: "Duration",
          type: "number",
        }),
      ]),
    );
  });

  it("adds a global Annotator property inside annotation filters", () => {
    const properties = buildTraceFilterProperties([
      {
        name: "latency",
        display_name: "Latency",
        category: "system_metric",
        source: "traces",
        type: "number",
      },
      {
        name: "label-1",
        display_name: "Quality",
        category: "annotation_metric",
        source: "both",
        output_type: "numeric",
      },
    ]);

    const annotator = properties.find(
      (property) => property.id === "annotator",
    );
    expect(annotator).toMatchObject({
      name: "Annotator",
      category: "annotation",
      type: "annotator",
      apiColType: "SYSTEM_METRIC",
      allowCustomValue: false,
    });

    const annotatorIndex = properties.findIndex(
      (property) => property.id === "annotator",
    );
    const labelIndex = properties.findIndex(
      (property) => property.id === "label-1",
    );
    expect(annotatorIndex).toBeLessThan(labelIndex);
  });

  it("maps every annotation label output type to the matching filter input type", () => {
    const properties = buildTraceFilterProperties([
      {
        name: "numeric-label",
        display_name: "Numeric",
        category: "annotation_metric",
        source: "both",
        output_type: "numeric",
      },
      {
        name: "star-label",
        display_name: "Star",
        category: "annotation_metric",
        source: "both",
        output_type: "star",
      },
      {
        name: "text-label",
        display_name: "Text",
        category: "annotation_metric",
        source: "both",
        output_type: "text",
      },
      {
        name: "thumbs-label",
        display_name: "Thumbs",
        category: "annotation_metric",
        source: "both",
        output_type: "thumbs_up_down",
      },
      {
        name: "category-label",
        display_name: "Category",
        category: "annotation_metric",
        source: "both",
        output_type: "categorical",
        choices: ["refund", "billing"],
      },
    ]);

    expect(properties).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "numeric-label", type: "number" }),
        expect.objectContaining({ id: "star-label", type: "number" }),
        expect.objectContaining({ id: "text-label", type: "text" }),
        expect.objectContaining({
          id: "thumbs-label",
          type: "thumbs",
          choices: ["Thumbs Up", "Thumbs Down"],
        }),
        expect.objectContaining({
          id: "category-label",
          type: "categorical",
          choices: ["refund", "billing"],
        }),
      ]),
    );
  });

  it("uses annotator email as secondary display text and searchable text", () => {
    const option = {
      value: "user-1",
      label: "Kartik",
      name: "Kartik",
      email: "kartik.nvj@futureagi.com",
      description: "kartik.nvj@futureagi.com",
    };

    expect(getPickerOptionSecondaryLabel(option)).toBe(
      "kartik.nvj@futureagi.com",
    );
    expect(getPickerOptionSearchText(option)).toContain("Kartik");
    expect(getPickerOptionSearchText(option)).toContain(
      "kartik.nvj@futureagi.com",
    );
    expect(
      getPickerOptionSecondaryLabel({
        value: "user-2",
        label: "reviewer@futureagi.com",
        email: "reviewer@futureagi.com",
      }),
    ).toBe("");
  });
});
