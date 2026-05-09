import { describe, it, expect } from "vitest";
import {
  getTraceFilterFields,
  normalizeFilterRowOperator,
} from "../TraceFilterPanel";

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
  it("maps API multi-value operators back to panel operators before apply", () => {
    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "in",
        value: ["OK"],
      }).operator,
    ).toBe("is");

    expect(
      normalizeFilterRowOperator({
        field: "status",
        fieldType: "categorical",
        operator: "not_in",
        value: ["ERROR"],
      }).operator,
    ).toBe("is_not");
  });

  it("maps backend number/date operators to valid panel operators", () => {
    expect(
      normalizeFilterRowOperator({
        field: "latency_ms",
        fieldType: "number",
        operator: "equals",
        value: "100",
      }).operator,
    ).toBe("equal_to");

    expect(
      normalizeFilterRowOperator({
        field: "created_at",
        fieldType: "date",
        operator: "less_than",
        value: "2026-05-09T00:00",
      }).operator,
    ).toBe("before");
  });

  it("falls back to the first valid operator for restricted id fields", () => {
    expect(
      normalizeFilterRowOperator({
        field: "trace_id",
        fieldType: "string",
        operator: "contains",
        value: "abc",
      }).operator,
    ).toBe("is");
  });
});
