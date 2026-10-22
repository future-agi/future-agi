import { describe, expect, it } from "vitest";

import {
  getExactAggregationReadState,
  getExactGraphData,
  getFilterValueReadMessage,
  getFilterValueReadState,
  getQueryReadState,
  hasValidStatusPair,
} from "src/utils/queryReadState";

// Older catalog APIs returned false/partial with coverage metadata. Preserve
// their declared status pair even though current APIs no longer emit it.
const partialPage = {
  query_complete: false,
  query_status: "partial",
  query_exact: false,
  query_provenance: "current_property_catalog",
  coverage_reason: "source_predates_index",
  coverage_floor: "2026-09-01 00:00:00.000000",
};

describe("older catalog partial-response compatibility", () => {
  it("is a valid status pair only when incomplete", () => {
    expect(hasValidStatusPair(partialPage)).toBe(true);
    expect(hasValidStatusPair({ ...partialPage, query_complete: true })).toBe(
      false,
    );
  });

  it("preserves the older incomplete-read presentation", () => {
    expect(getQueryReadState(partialPage)).toBe("degraded");
    expect(getFilterValueReadState(partialPage)).toBe("degraded");
  });
});

describe("current catalog successful-page semantics", () => {
  it.each([
    { values: [] },
    { values: [{ value: "observed", type: "string" }] },
  ])(
    "keeps successful suggestions usable without a notice: %j",
    ({ values }) => {
      const page = {
        values,
        query_complete: true,
        query_status: "complete",
        query_exact: false,
        query_provenance: "current_property_catalog",
      };
      expect(hasValidStatusPair(page)).toBe(true);
      expect(getQueryReadState(page)).toBe("complete");
      expect(
        getFilterValueReadMessage(getFilterValueReadState(page)),
      ).toBeNull();

      // A successful suggestion page is not an exact-aggregation response.
      const graph = { ...page, data: [{ value: 1 }] };
      expect(getExactAggregationReadState(graph)).toBe("degraded");
      expect(getExactGraphData(graph)).toEqual([]);
    },
  );

  it("does not mask contradictory success metadata with current provenance", () => {
    const page = {
      values: [],
      query_complete: true,
      query_status: "complete",
      query_exact: false,
      query_provenance: "current_property_catalog",
      query_error_code: "query_failed",
    };
    expect(hasValidStatusPair(page)).toBe(false);
    expect(getFilterValueReadState(page)).toBe("degraded");
    expect(getFilterValueReadMessage(getFilterValueReadState(page))).toMatch(
      /unavailable/i,
    );
  });
});
