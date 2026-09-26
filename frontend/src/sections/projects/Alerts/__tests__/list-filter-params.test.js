import { describe, expect, it } from "vitest";

import {
  ALERT_LIST_MULTI_VALUE_FIELDS,
  buildFilterParams,
} from "../store/alertFilterState";

const buildAlertFilterParams = (f) =>
  buildFilterParams(f, ALERT_LIST_MULTI_VALUE_FIELDS);
const buildIssueFilterParams = (f) => buildFilterParams(f);

// FilterPanel always emits `{field: [values]}`. The monitor-list endpoint still
// expects scalars for the single-select fields and a repeated param only for
// project_id, so the translation has to survive the swap to the shared panel.
describe("alerts list filter params", () => {
  it("unwraps single-select fields back to scalars", () => {
    expect(
      buildAlertFilterParams({
        metric_type: ["span_response_time"],
        status: ["triggered"],
      }),
    ).toEqual({
      metric_type: "span_response_time",
      status: "triggered",
    });
  });

  it("keeps project_id as an array for repeated query params", () => {
    expect(
      buildAlertFilterParams({ project_id: ["proj-1", "proj-2"] }),
    ).toEqual({ project_id: ["proj-1", "proj-2"] });
  });

  it("drops fields with no selected values", () => {
    expect(
      buildAlertFilterParams({ metric_type: [], status: ["healthy"] }),
    ).toEqual({ status: "healthy" });
  });

  it("returns null when nothing is filtered", () => {
    expect(buildAlertFilterParams(null)).toBeNull();
    expect(buildAlertFilterParams({})).toBeNull();
    expect(buildAlertFilterParams({ metric_type: [] })).toBeNull();
  });
});

// The issues table inside the alert detail sheet has its own endpoint, which
// takes the trigger type as a scalar.
describe("alert issues filter params", () => {
  it("unwraps the trigger type to a scalar", () => {
    expect(buildIssueFilterParams({ type: ["critical"] })).toEqual({
      type: "critical",
    });
  });

  it("returns null when nothing is filtered", () => {
    expect(buildIssueFilterParams(null)).toBeNull();
    expect(buildIssueFilterParams({ type: [] })).toBeNull();
  });

  it("unwraps every field, including ones the list sends as arrays", () => {
    // The two builders share an implementation; only the list passes a
    // multi-value set. Pin that the issues table applies none of it.
    expect(buildIssueFilterParams({ project_id: ["a", "b"] })).toEqual({
      project_id: "a",
    });
  });

  it("keeps a falsy first value rather than dropping the field", () => {
    expect(buildIssueFilterParams({ type: [""] })).toEqual({ type: "" });
    expect(buildAlertFilterParams({ status: [0] })).toEqual({ status: 0 });
  });
});
